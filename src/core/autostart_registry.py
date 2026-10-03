r"""src/core/autostart_registry — Windows 开机自启注册表管理。

通过 HKCU\Software\Microsoft\Windows\CurrentVersion\Run 写入/移除自启项。
仅当前用户可见，不需要管理员权限。
"""

import logging
import sys
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
