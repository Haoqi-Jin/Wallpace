r"""src/core/autostart_registry — Windows 开机自启注册表管理。

通过 HKCU\Software\Microsoft\Windows\CurrentVersion\Run 写入/移除自启项。
仅当前用户可见，不需要管理员权限。
"""

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: nocover
    pass

logger = logging.getLogger(__name__)

_REG_KEY_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Wallpace"


def _get_exe_path() -> str:
    """获取当前可执行文件路径（开发阶段用 python.exe，打包后用 pyinstaller 解包路径）。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.executable


class AutostartManager:
    """管理 HKCU 注册表中的开机自启项。

    用法:
        mgr = AutostartManager()
        mgr.enable()   # 写入注册表
        mgr.disable()  # 移除注册表
        enabled = mgr.is_enabled  # 查询状态（属性）
    """

    def __init__(self) -> None:
        try:
            import winreg as _winreg
        except ImportError:
            logger.warning("winreg 不可用（非 Windows 平台）")
            self._winreg = None
            self._reg_key = None
            return

        self._winreg = _winreg
        try:
            self._reg_key = _winreg.OpenKey(
                _winreg.HKEY_CURRENT_USER,
                _REG_KEY_PATH,
                0,
                _winreg.KEY_READ | _winreg.KEY_SET_VALUE,
            )
        except OSError as exc:
            logger.error("无法打开注册表: %s", exc)
            self._reg_key = None

    @property
    def is_enabled(self) -> bool:
        """检查是否已在注册表中注册为自启。"""
        if self._reg_key is None or self._winreg is None:
            return False
        try:
            self._winreg.QueryValueEx(self._reg_key, _APP_NAME)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def enable(self) -> bool:
        """注册开机自启。返回是否成功。"""
        if self._reg_key is None or self._winreg is None:
            logger.error("注册表不可用，无法启用自启")
            return False
        try:
            exe_path = _get_exe_path()
            # 开发与打包态都带 --hidden：开机自启一律进托盘，不弹主窗口（像微信）
            args = "--hidden"
            value = f'"{exe_path}"{f" {args}" if args else ""}'
            self._winreg.SetValueEx(
                self._reg_key, _APP_NAME, 0,
                self._winreg.REG_SZ, value,
            )
            logger.info("已注册开机自启: %s", value)
            return True
        except OSError as exc:
            logger.error("注册自启失败: %s", exc)
            return False

    def is_valid(self) -> bool:
        """检查已注册的自启项是否仍指向存在的可执行文件（G02）。

        删掉 wallpace.exe / 换目录重装后，注册表里残留的旧路径
        会让开机自启变成"坏启动项"（任务管理器里能看到、开机静默报错）。
        本方法读出注册表值并校验其中引用的可执行文件是否存在；
        无自启项或非 Windows 平台返回 False（视为无效，交由调用方决定是否清理）。

        Returns:
            自启项有效（路径存在）返回 True，否则 False。
        """
        if self._reg_key is None or self._winreg is None:
            return False
        try:
            value = str(self._winreg.QueryValueEx(self._reg_key, _APP_NAME)[0])
        except (FileNotFoundError, OSError):
            return False
        # 值形如 "C:\path\to\wallpace.exe" --hidden（见 enable() 的写入格式）。
        # 取第一个空白分隔片段，并剥掉可能的外层引号。
        path_part = value.strip().split()[0].strip('"') if value.strip() else ""
        if not path_part:
            return False
        return Path(path_part).exists()

    def cleanup_invalid(self) -> bool:
        """移除指向不存在文件的坏自启项（G02 启动自愈）。

        Returns:
            确实移除了一项返回 True；本无自启项或项仍然有效时返回 False。
        """
        if not self.is_enabled:
            return False
        if self.is_valid():
            return False
        return self.disable()

    def disable(self) -> bool:
        """移除开机自启注册。返回是否成功。"""
        if self._reg_key is None or self._winreg is None:
            return False
        try:
            self._winreg.DeleteValue(self._reg_key, _APP_NAME)
            logger.info("已移除开机自启注册")
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.error("移除自启注册失败: %s", exc)
            return False
