"""tests/test_wallpaper_manager.py -- WallpaperManager module tests."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.desktop_wallpaper_com import MonitorInfo
from core.wallpaper_manager import (
    SPI_SETDESKWALLPAPER,
    WallpaperManager,
)


class _FakeCOM:
    """可控的 DesktopWallpaperCOM 替身（真实 COM 在测试环境不可用）。"""

    def __init__(self, available=True, monitors=None, fail=False, raise_exc=False):
        self._available = available
        self._monitors = list(monitors or [])
        self._fail = fail
        self._raise = raise_exc
        self.calls = []

    def is_available(self):
        return self._available

    def list_monitors(self):
        return list(self._monitors)

    def set_wallpaper(self, monitor_id, image_path):
        self.calls.append((monitor_id, image_path))
        if self._raise:
            raise RuntimeError("boom")
        return not self._fail

    def release(self):
        pass


class TestWallpaperManagerBasic:
    @pytest.fixture()
    def wm(self):
        return WallpaperManager()

    def test_is_supported_returns_bool(self, wm):
        assert isinstance(wm.is_supported, bool)

    def test_verify_set_fails_when_not_supported(self):
        orig = sys.platform
        try:
            sys.platform = "linux"
            if "core.wallpaper_manager" in sys.modules:
                del sys.modules["core.wallpaper_manager"]
            from core.wallpaper_manager import WallpaperManager as WM2
            wm2 = WM2()
            assert not wm2.is_supported
        finally:
            sys.platform = orig


class TestWallpaperManagerGetCurrent:
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Requires Windows environment",
    )
    def test_get_current_on_windows_real(self):
        wm = WallpaperManager()
        current = wm.get_current_wallpaper()
        assert current is None or isinstance(current, str)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Skip on Windows",
    )
    def test_returns_none_when_not_supported(self):
        wm = WallpaperManager()
        assert not wm.is_supported
        assert wm.get_current_wallpaper() is None


class TestWallpaperManagerEdgeCases:
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Requires Windows",
    )
    def test_set_nonexistent_image(self):
        wm = WallpaperManager()
        result = wm.set_wallpaper("/nonexistent/path/image.jpg")
        assert result is False


class TestWallpaperManagerPerMonitor:
    """#7 逐屏壁纸：以假 COM 替身覆盖分支与降级（真实 COM 无法在 CI 验证）。"""

    @pytest.fixture()
    def wm(self):
        wm = WallpaperManager()
        wm._is_windows = True  # 强制走 Windows 分支，不依赖真实平台
        return wm

    def test_set_wallpapers_all_success(self, wm, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        fake = _FakeCOM(available=True)
        wm._com = fake
        ok = wm.set_wallpapers({"M1": str(a), "M2": str(b)})
        assert ok is True
        assert fake.calls == [("M1", str(a)), ("M2", str(b))]

    def test_set_wallpapers_empty_key_means_all_monitors(self, wm, tmp_path):
        """monitor_id == "" 必须原样透传给 COM（表示全部显示器）。"""
        a = tmp_path / "a.png"
        a.write_bytes(b"x")
        fake = _FakeCOM(available=True)
        wm._com = fake
        assert wm.set_wallpapers({"": str(a)}) is True
        assert fake.calls == [("", str(a))]

    def test_set_wallpapers_empty_mapping_is_false(self, wm):
        wm._com = _FakeCOM(available=True)
        assert wm.set_wallpapers({}) is False

    def test_list_monitors_unavailable_returns_empty(self, wm):
        wm._com = _FakeCOM(available=False)
        assert wm.list_monitors() == []

    def test_list_monitors_maps_to_dicts(self, wm):
        wm._com = _FakeCOM(
            available=True,
            monitors=[MonitorInfo("M1", "M1"), MonitorInfo("M2", "M2")],
        )
        assert wm.list_monitors() == [
            {"id": "M1", "device_path": "M1"},
            {"id": "M2", "device_path": "M2"},
        ]

    def test_is_multi_monitor_supported(self, wm):
        wm._com = _FakeCOM(available=True)
        assert wm.is_multi_monitor_supported is True
        wm._com = _FakeCOM(available=False)
        assert wm.is_multi_monitor_supported is False
        wm._is_windows = False
        assert wm.is_multi_monitor_supported is False

    @pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows SPI")
    def test_set_wallpapers_fallback_on_failure(self, wm, tmp_path):
        """COM 逐屏设置失败 → 返回 False 且确实回退调用 SPI。"""
        a = tmp_path / "a.png"
        a.write_bytes(b"x")
        wm._com = _FakeCOM(available=True, fail=True)
        with patch(
            "ctypes.windll.user32.SystemParametersInfoW", return_value=1
        ) as spi:
            ok = wm.set_wallpapers({"M1": str(a)})
        assert ok is False
        assert spi.called

    @pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows SPI")
    def test_set_wallpapers_fallback_on_exception(self, wm, tmp_path):
        """COM 逐屏设置抛异常 → 吞掉、返回 False 且回退 SPI（绝不冒泡）。"""
        a = tmp_path / "a.png"
        a.write_bytes(b"x")
        wm._com = _FakeCOM(available=True, raise_exc=True)
        with patch(
            "ctypes.windll.user32.SystemParametersInfoW", return_value=1
        ) as spi:
            ok = wm.set_wallpapers({"M1": str(a)})
        assert ok is False
        assert spi.called

    @pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows SPI")
    def test_set_wallpapers_unavailable_falls_back_to_spi(self, wm, tmp_path):
        """COM 不可用 → 降级为统一 SPI 设置并返回 False。"""
        a = tmp_path / "a.png"
        a.write_bytes(b"x")
        wm._com = _FakeCOM(available=False)
        with patch(
            "ctypes.windll.user32.SystemParametersInfoW", return_value=1
        ) as spi:
            ok = wm.set_wallpapers({"M1": str(a)})
        assert ok is False
        assert spi.called
