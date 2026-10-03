"""tests/test_desktop_wallpaper_com.py — IDesktopWallpaper COM 封装测试。

真实 COM（CoCreateInstance 成功 + 多显示器）无法在 offscreen / CI / 无桌面
会话下验证，本文件只验证**降级安全契约**：任何方法在任何环境都不得抛异常，
不可用时优雅返回 ``False`` / ``[]``，且 ``release()`` 幂等。
"""

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.desktop_wallpaper_com import DesktopWallpaperCOM, MonitorInfo


class TestDesktopWallpaperCOMDegradation:
    def test_is_available_returns_bool_and_never_raises(self):
        """is_available 必须返回 bool 且绝不抛（任意平台）。"""
        com = DesktopWallpaperCOM()
        result = com.is_available()
        assert isinstance(result, bool)

    def test_construct_does_not_touch_com(self):
        """构造函数不得触碰 COM（否则非 Windows 下直接炸）。"""
        com = DesktopWallpaperCOM()
        assert com._available is None  # 尚未探测
        assert com._ptr is None

    def test_forced_non_windows_env_degrades(self, monkeypatch):
        """把平台伪装成非 Windows：全部返回降级值且不抛。"""
        monkeypatch.setattr(sys, "platform", "linux")
        com = DesktopWallpaperCOM()
        assert com.is_available() is False
        assert com.list_monitors() == []
        assert com.set_wallpaper("", "C:/whatever.png") is False

    def test_unavailable_env_returns_false_and_empty(self):
        """当前环境若 COM 不可用，则 list/set 必须优雅降级（绝不抛）。"""
        com = DesktopWallpaperCOM()
        if com.is_available():
            pytest.skip("本机提供真实 IDesktopWallpaper，跳过不可用路径断言")
        assert com.list_monitors() == []
        assert com.set_wallpaper("", str(Path(__file__))) is False

    def test_release_is_idempotent(self):
        """release 连调两次不得抛异常（幂等）。"""
        com = DesktopWallpaperCOM()
        com.release()
        com.release()


class TestMonitorInfo:
    def test_frozen_dataclass(self):
        mon = MonitorInfo(id="dev-1", device_path="dev-1")
        assert mon.id == "dev-1"
        assert mon.device_path == "dev-1"
        with pytest.raises(dataclasses.FrozenInstanceError):
            mon.id = "other"  # frozen：不可变
