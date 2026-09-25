"""src/core/desktop_wallpaper_com.py — IDesktopWallpaper COM 封装（纯 ctypes）。

用于「多显示器逐屏壁纸」：调用 Windows Shell 的 ``IDesktopWallpaper`` 接口
（``CLSID_DesktopWallpaper`` / ``IID_IDesktopWallpaper``）为每块显示器单独
设置壁纸（全局 SPI_SETDESKWALLPAPER 只能统一设置所有显示器）。

设计要点
--------
* **零新增依赖**：不引入 comtypes / pywin32，直接用 ctypes 手写 COM 调用。
* **惰性初始化**：``__init__`` 绝不触碰 COM（不 ``CoInitialize``、不
  ``CoCreateInstance``）。只在首次调用 ``is_available()`` / ``list_monitors()`` /
  ``set_wallpaper()`` 时才初始化；失败会**记住失败状态**，避免每次重试。
  这样 ``DesktopWallpaperCOM()`` 在非 Windows / 无桌面会话 / CI 下都能安全构造。
* **绝不抛异常**：任何异常都被吞掉并返回 ``False`` / ``[]``，由调用方优雅降级
  （统一模式继续走 SPI），绝不让底层 COM 问题冒泡到 UI。

vtable 槽位对照表（IUnknown 之后）
---------------------------------
依据 Windows SDK ``shobjidl_core.h`` 中 ``IDesktopWallpaper`` 的方法声明顺序：

    idx 0 QueryInterface         idx 1 AddRef                  idx 2 Release
    idx 3 SetWallpaper           idx 4 GetWallpaper            idx 5 GetMonitorDevicePathAt
    idx 6 GetMonitorDevicePathCount                           idx 7 GetMonitorRECT
    idx 8 SetBackgroundColor                                   idx 9 GetBackgroundColor
    idx 10 SetPosition           idx 11 GetPosition
    idx 12 SetSlideshow          idx 13 GetSlideshow
    idx 14 SetSlideshowOptions                                 idx 15 GetSlideshowOptions
    idx 16 AdvanceSlideshow                                    idx 17 GetStatus
    idx 18 Enable

**槽位顺序决定一切**：顺序错就会调用到别的函数、直接崩进程。本模块只实现
必需的三处（``SetWallpaper=3`` / ``GetMonitorDevicePathAt=5`` /
``GetMonitorDevicePathCount=6``）并按上表硬编码索引。

依据来源（已核对）：
  * https://learn.microsoft.com/windows/win32/api/shobjidl_core/nn-shobjidl_core-idesktopwallpaper
  * https://docs.rs/winapi/latest/winapi/um/shobjidl_core/struct.IDesktopWallpaper.html
  * CLSID ``{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}``、IID ``{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}``

关于字符串所有权：``SetWallpaper`` 的两个参数是 ``LPCWSTR``（入参，不需释放）；
``GetMonitorDevicePathAt`` 通过 ``LPWSTR*`` 返回的字符串由**调用方**负责用
``CoTaskMemFree`` 释放（否则泄漏），本模块已释放。
"""

import ctypes
import logging
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# GUID / CLSID（来源见模块 docstring）
CLSID_DesktopWallpaper = "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}"
IID_IDesktopWallpaper = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"

# CoCreateInstance 的 CLSCTX 与 CoInitializeEx 的模式常量
CLSCTX_ALL = 0x17  # INPROC_SERVER | INPROC_HANDLER | LOCAL_SERVER | REMOTE_SERVER
COINIT_APARTMENTTHREADED = 0x2

# vtable 槽位（IUnknown 之后），见模块 docstring 对照表
_VTBL_RELEASE = 2
_VTBL_SET_WALLPAPER = 3
_VTBL_GET_MONITOR_DEVICE_PATH_AT = 5
_VTBL_GET_MONITOR_DEVICE_PATH_COUNT = 6


class _GUID(ctypes.Structure):
    """COM GUID 的二进制布局（与 ``uuid.UUID.bytes_le`` 一致）。"""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> "_GUID":
    """由形如 ``{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}`` 的字符串构造 _GUID。"""
    guid = _GUID()
    ctypes.memmove(ctypes.byref(guid), uuid.UUID(text).bytes_le, 16)
    return guid


_CLSID = _guid(CLSID_DesktopWallpaper)
_IID = _guid(IID_IDesktopWallpaper)


@dataclass(frozen=True)
class MonitorInfo:
    """一块显示器的信息。

    Attributes:
        id: ``GetMonitorDevicePathAt`` 返回的设备路径，作为 monitor id 使用。
        device_path: 与 ``id`` 相同；保留独立字段便于将来接入 QScreen 名称/序号。
    """

    id: str
    device_path: str


class DesktopWallpaperCOM:
    """``IDesktopWallpaper`` 接口的 ctypes 封装（惰性初始化 + 全路径降级）。

    任何方法在任何环境下都不抛异常：不可用时返回 ``False`` / ``[]``。
    """

    def __init__(self) -> None:
        # 注意：构造函数绝不触碰 COM，保证非 Windows / 无桌面会话下可安全构造。
        self._ptr: Optional[ctypes.c_void_p] = None
        self._co_initialized: bool = False  # 是否需要由我们 CoUninitialize
        self._available: Optional[bool] = None  # 三态：None=尚未探测
        self._lock = threading.Lock()

    # ==================== 公开 API ====================

    def is_available(self) -> bool:
        """COM 接口是否可用。

        非 Windows / ``CoCreateInstance`` 失败 / 任意异常一律返回 ``False``，
        绝不抛。首次探测的结果会被缓存（失败也缓存，避免每次重试）。
        """
        if self._available is None:
            with self._lock:
                if self._available is None:
                    self._available = self._initialize()
        return bool(self._available)

    def list_monitors(self) -> List[MonitorInfo]:
        """枚举系统显示器。

        Returns:
            ``MonitorInfo`` 列表；不可用或失败时返回 ``[]``（绝不抛）。
        """
        if not self.is_available():
            return []
        try:
            count = ctypes.c_uint(0)
            hr = self._call(
                _VTBL_GET_MONITOR_DEVICE_PATH_COUNT,
                ctypes.c_int,
                [ctypes.POINTER(ctypes.c_uint)],
                ctypes.byref(count),
            )
            if hr != 0:
                logger.warning(
                    "GetMonitorDevicePathCount 失败: hr=0x%08X", hr & 0xFFFFFFFF
                )
                return []
            monitors: List[MonitorInfo] = []
            for index in range(int(count.value)):
                device_path = self._get_monitor_path(index)
                if device_path:
                    monitors.append(
                        MonitorInfo(id=device_path, device_path=device_path)
                    )
            return monitors
        except Exception:
            logger.warning("枚举显示器异常，降级返回空列表", exc_info=True)
            return []

    def set_wallpaper(self, monitor_id: str, image_path: str) -> bool:
        """为指定显示器设置壁纸。

        Args:
            monitor_id: 显示器设备路径；传入 ``""`` 表示作用于全部显示器
                （``IDesktopWallpaper`` 的约定语义）。
            image_path: 图片绝对路径。

        Returns:
            True 表示成功；不可用或失败返回 False（绝不抛）。
        """
        if not self.is_available():
            return False
        try:
            path = str(Path(image_path).resolve())
            hr = self._call(
                _VTBL_SET_WALLPAPER,
                ctypes.c_int,
                [ctypes.c_wchar_p, ctypes.c_wchar_p],
                monitor_id or "",
                path,
            )
            if hr != 0:
                logger.warning(
                    "SetWallpaper 失败: monitor=%r hr=0x%08X",
                    monitor_id,
                    hr & 0xFFFFFFFF,
                )
                return False
            return True
        except Exception:
            logger.warning(
                "SetWallpaper 异常: monitor=%r", monitor_id, exc_info=True
            )
            return False

    def release(self) -> None:
        """释放 COM 接口并（若由本对象初始化）``CoUninitialize``；幂等。

        可在关闭窗口时调用以避免对象泄漏；重复调用安全。
        """
        with self._lock:
            if self._ptr is not None:
                try:
                    self._call(_VTBL_RELEASE, ctypes.c_ulong, [])
                except Exception:
                    logger.debug("Release 调用异常（忽略）", exc_info=True)
                self._ptr = None
            if self._co_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    logger.debug("CoUninitialize 异常（忽略）", exc_info=True)
                self._co_initialized = False
            # 释放后允许未来重新探测（下次 is_available 会重新初始化）。
            self._available = None

    # ==================== 私有实现 ====================

    def _initialize(self) -> bool:
        """惰性初始化 COM 并创建 IDesktopWallpaper 实例；失败返回 False。"""
        if sys.platform != "win32":
            return False

        try:
            ole32 = ctypes.windll.ole32

            ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            ole32.CoInitializeEx.restype = ctypes.c_int
            hr_init = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            # S_OK(0)/S_FALSE(1)：本线程 COM 已就绪，由我们负责 CoUninitialize。
            # RPC_E_CHANGED_MODE(0x80010106)：已被别的模型初始化，仍可继续，
            # 但不归我们收尾（不调用 CoUninitialize）。
            self._co_initialized = hr_init in (0, 1)

            ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(_GUID),
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            ole32.CoCreateInstance.restype = ctypes.c_int
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(_CLSID),
                None,
                CLSCTX_ALL,
                ctypes.byref(_IID),
                ctypes.byref(ptr),
            )
            if hr != 0 or not ptr.value:
                logger.warning(
                    "IDesktopWallpaper 不可用 (CoCreateInstance hr=0x%08X)",
                    hr & 0xFFFFFFFF,
                )
                return False

            self._ptr = ptr
            logger.info("IDesktopWallpaper COM 初始化成功")
            return True
        except Exception:
            logger.warning("初始化 IDesktopWallpaper COM 失败", exc_info=True)
            return False

    def _get_monitor_path(self, index: int) -> Optional[str]:
        """取第 ``index`` 个显示器的设备路径；失败返回 None。

        ``GetMonitorDevicePathAt`` 返回的字符串需由调用方 ``CoTaskMemFree``，
        本方法在 ``finally`` 中释放，避免泄漏。
        """
        raw = ctypes.c_void_p()
        hr = self._call(
            _VTBL_GET_MONITOR_DEVICE_PATH_AT,
            ctypes.c_int,
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
            index,
            ctypes.byref(raw),
        )
        if hr != 0 or not raw.value:
            logger.warning(
                "GetMonitorDevicePathAt(%d) 失败: hr=0x%08X", index, hr & 0xFFFFFFFF
            )
            return None
        try:
            return ctypes.wstring_at(raw)
        finally:
            try:
                ctypes.windll.ole32.CoTaskMemFree(raw)
            except Exception:
                logger.debug("CoTaskMemFree 失败（忽略）", exc_info=True)

    def _call(self, index: int, restype, argtypes, *args):
        """按 vtable 槽位 ``index`` 调用 COM 方法（this 指针自动作为首参）。

        Args:
            index: vtable 槽位下标（见模块 docstring 对照表）。
            restype: 返回类型（HRESULT 用 ``ctypes.c_int``）。
            argtypes: 除 this 指针外各参数的类型列表。
            *args: 实参。

        Returns:
            被调用方法的返回值。
        """
        interface = self._ptr
        if interface is None or not interface.value:
            raise RuntimeError("COM 接口未初始化或已释放")

        vtable = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        func = prototype(vtable[index])
        return func(interface, *args)
