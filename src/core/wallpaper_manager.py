"""src/core/wallpaper_manager.py — 壁纸设置模块。

通过 ctypes 调用 Windows SPI_SETDESKWALLPAPER API 设置系统壁纸。
支持多显示器（API 自动统一设置）。
无 GUI 依赖，纯系统级操作。
"""

import ctypes
import logging
import sys
from pathlib import Path

from src.core.desktop_wallpaper_com import DesktopWallpaperCOM

# Windows 常量
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDWININICHANGE = 0x02

logger = logging.getLogger(__name__)


class WallpaperManager:
    """Windows 壁纸设置管理器。

    封装 SystemParametersInfoW 调用，提供设置/读取当前壁纸的能力。
    仅可在 Windows 上运行；在非 Windows 平台会自动降级。
    """

    def __init__(self) -> None:
        """初始化壁纸管理器。"""
        self._is_windows = sys.platform == "win32"
        # 逐屏壁纸用 COM 封装。注意：这里的构造**不触碰 COM**（惰性初始化），
        # 因此非 Windows / 无桌面会话下也能安全构造本对象。
        self._com = DesktopWallpaperCOM()
        if not self._is_windows:
            logger.warning("非 Windows 平台，壁纸设置将不可用")

    # ==================== 核心方法 ====================

    def set_wallpaper(self, image_path: str) -> bool:
        """设置系统壁纸为指定图片。

        Args:
            image_path: 图片的绝对路径。

        Returns:
            True 表示成功设置；False 表示失败或不可用。
        """
        if not self._is_windows:
            logger.error("不支持非 Windows 平台")
            return False

        path_obj = Path(image_path)
        if not path_obj.exists():
            logger.error("图片文件不存在: %s", image_path)
            return False

        try:
            # 转为宽字符路径（Windows API 需要）
            wide_path = str(path_obj.resolve())

            result = ctypes.windll.user32.SystemParametersInfoW(
                SPI_SETDESKWALLPAPER,
                0,
                wide_path,
                SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE,
            )

            if result:
                logger.info("壁纸已设置为: %s", wide_path)
                return True
            else:
                error_code = ctypes.GetLastError()
                logger.error(
                    "SystemParametersInfoW 返回失败, code=%d", error_code
                )
                return False

        except AttributeError as exc:
            logger.error("找不到 SystemParametersInfoW: %s", exc)
            return False
        except OSError as exc:
            logger.error("调用 Windows API 出错: %s", exc)
            return False

    def set_wallpapers(self, mapping: dict[str, str]) -> bool:
        """逐屏设置壁纸：``{monitor_id: image_path}``，key ``""`` 表示全部显示器。

        全部成功返回 True；任一失败/异常都返回 False，并**回退为统一 SPI 设置**
        （取 mapping 中任意一张图），保证用户至少能看到壁纸变化，而不是静默失败。

        Args:
            mapping: 显示器设备路径 → 图片绝对路径 的映射。

        Returns:
            是否全部设置成功；非 Windows / 空映射 / COM 不可用一律 False。
        """
        if not self._is_windows:
            logger.warning("非 Windows 平台，无法设置逐屏壁纸")
            return False
        if not mapping:
            logger.warning("逐屏映射为空，跳过")
            return False

        fallback = next(iter(mapping.values()), None)

        # COM 不可用：直接降级为统一（整屏）SPI 设置。
        if not self._com.is_available():
            logger.warning("IDesktopWallpaper 不可用，逐屏壁纸降级为统一设置")
            if fallback:
                self.set_wallpaper(fallback)
            return False

        all_ok = True
        try:
            for monitor_id, image_path in mapping.items():
                if not self._com.set_wallpaper(monitor_id, image_path):
                    logger.warning("显示器 %r 设置壁纸失败", monitor_id)
                    all_ok = False
        except Exception:
            # 兜底：COM 层理论上不抛，但防范任何底层异常冒泡到 UI。
            logger.warning("逐屏壁纸设置异常，回退统一设置", exc_info=True)
            all_ok = False

        if not all_ok and fallback:
            logger.warning("逐屏设置未全部成功，回退整屏 SPI 设置")
            self.set_wallpaper(fallback)
        return all_ok

    def list_monitors(self) -> list[dict]:
        """枚举系统显示器。

        Returns:
            ``[{"id": ..., "device_path": ...}, ...]``；非 Windows / COM 不可用时 []。
        """
        if not self._is_windows:
            return []
        return [
            {"id": mon.id, "device_path": mon.device_path}
            for mon in self._com.list_monitors()
        ]

    def release(self) -> None:
        """释放底层 COM 资源（幂等）；非 Windows 下为空操作。

        应在应用退出（``closeEvent``）时调用，避免 COM 对象泄漏。
        """
        self._com.release()

    def get_current_wallpaper(self) -> str | None:
        """尝试读取当前系统壁纸路径。

        从注册表 HKCU\\Control Panel\\Desktop\\WallPaper 读取。

        Returns:
            当前壁纸路径，或 None（无法获取时）。
        """
        if not self._is_windows:
            return None

        try:
            import winreg
        except ImportError:
            logger.warning("winreg 不可用")
            return None

        key_path = r"Control Panel\Desktop"
        value_name = "WallPaper"
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ
            ) as key:
                wallpaper, _ = winreg.QueryValueEx(key, value_name)
                return str(wallpaper) if wallpaper else None
        except OSError:
            logger.warning("无法读取当前壁纸路径")
            return None

    def verify_set(self, image_path: str) -> bool:
        """先尝试设置，然后验证是否生效。

        Args:
            image_path: 要设置的图片路径。

        Returns:
            设置成功且验证一致则返回 True。
        """
        success = self.set_wallpaper(image_path)
        if not success:
            return False

        current = self.get_current_wallpaper()
        if current and str(Path(image_path).resolve()) == str(Path(current).resolve()):
            return True

        # 注册表可能更新有延迟，允许一定宽容度
        resolved_input = str(Path(image_path).resolve())
        if current is None or resolved_input in current or current in resolved_input:
            logger.debug("壁纸设置后验证通过（路径匹配）")
            return True

        logger.debug("壁纸设置后验证不一致: %s != %s", resolved_input, current)
        return False

    @property
    def is_supported(self) -> bool:
        """返回当前平台是否支持壁纸设置。"""
        return self._is_windows

    @property
    def is_multi_monitor_supported(self) -> bool:
        """是否支持逐屏壁纸（Windows 且 ``IDesktopWallpaper`` COM 可用）。

        注意：此属性只反映「COM 能力」；是否真的有多块显示器需另行检查
        ``list_monitors()`` 的数量（UI 会同时要求 ``len(list_monitors()) >= 2``）。
        惰性探测：非 Windows 直接 False，不触碰 COM。
        """
        if not self._is_windows:
            return False
        return self._com.is_available()
