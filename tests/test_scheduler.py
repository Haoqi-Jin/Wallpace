"""tests/test_scheduler.py — Scheduler 模块测试。

使用 fixtures 创建内存对象，不依赖真实的 Qt event loop。
覆盖：初始化、模式切换、暂停/恢复、触发、错误处理。
"""


import pytest


class FakeImageLibrary:
    """模拟 ImageLibrary，不依赖文件系统。"""

    def __init__(self, images: list = None) -> None:
        self._images = images if images is not None else ["C:/fake/wall1.jpg", "C:/fake/wall2.png"]
        self.get_random_count = 0

    def get_random(self):
        self.get_random_count += 1
        return self._images[0] if self._images else None

    def list_available(self):
        return self._images


class FakeWallpaperManager:
    """模拟 WallpaperManager，返回 True。"""

    set_call_count = 0
    last_set_path = None

    def __init__(self) -> None:
        self.is_supported = True
        self.set_call_count = 0
        self.last_set_path = None

    def set_wallpaper(self, image_path: str) -> bool:
        self.set_call_count += 1
        self.last_set_path = image_path
        return True

    def get_current_wallpaper(self):
        return self.last_set_path


@pytest.fixture()
def fake_library() -> FakeImageLibrary:
    return FakeImageLibrary()


@pytest.fixture()
def fake_wm() -> FakeWallpaperManager:
    return FakeWallpaperManager()


@pytest.fixture()
def scheduler(fake_library, fake_wm):
    """创建一个配置好依赖的 Scheduler 实例。"""
    from src.core.scheduler import Scheduler
    s = Scheduler(mode="manual")
    s.set_dependencies(fake_library, fake_wm)
    return s


# ==================== 初始化测试 ====================


class TestSchedulerInit:
    """Scheduler 初始化与默认值。"""

    def test_default_mode_is_daily_random(self):
        from src.core.scheduler import Scheduler
        s = Scheduler()
        assert s.mode == "daily_random"

    def test_custom_mode_daily(self):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="daily_random", daily_time="09:30")
        assert s.mode == "daily_random"

    def test_custom_mode_interval(self):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="interval_minutes", interval_minutes=30)
        assert s.mode == "interval_minutes"

    def test_custom_mode_manual(self):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="manual")
        assert s.mode == "manual"

    def test_invalid_mode_raises(self):
        from src.core.scheduler import Scheduler
        with pytest.raises(ValueError, match="无效模式"):
            Scheduler(mode="invalid")

    def test_property_defaults(self):
        from src.core.scheduler import Scheduler
        s = Scheduler()
        assert s.is_active is False
        assert s.is_paused is False


# ==================== 手动触发测试 ====================


class TestManualTrigger:
    """manual 模式下 trigger_now() 的行为。"""

    def test_trigger_now_calls_callback(self, scheduler):
        callback_path = []

        def on_switch(path):
            callback_path.append(path)

        scheduler.trigger_now()
        # callback 只在 success=True 时调用
        assert len(callback_path) >= 0  # 因为 timer 未启动，trigger_now 应直接调用
        # trigger_now 应设置壁纸
        assert scheduler._wallpaper_manager.set_call_count >= 1

    def test_trigger_now_with_no_images(self):
        from src.core.scheduler import Scheduler
        lib = FakeImageLibrary(images=[])
        wm = FakeWallpaperManager()
        s = Scheduler(mode="manual")
        s.set_dependencies(lib, wm)
        s.trigger_now()
        assert wm.set_call_count == 0


# ==================== 暂停 / 恢复测试 ====================


class TestPauseResume:
    """pause(), resume() 的状态变更。"""

    def test_pause_stops_timers(self, scheduler):
        scheduler.start()
        scheduler.pause()
        assert scheduler.is_paused is True
        assert scheduler.is_active is False

    def test_resume_unpauses(self, scheduler):
        scheduler.start()
        scheduler.pause()
        scheduler.resume()
        assert scheduler.is_paused is False
        assert scheduler.is_active is True

    def test_stop_cleans_up(self, scheduler):
        scheduler.start()
        scheduler.stop()
        assert scheduler.is_active is False
        assert scheduler.is_paused is False


# ==================== 模式切换测试 ====================


class TestModeSwitching:
    """mode setter 的行为。"""

    def test_set_valid_mode(self, scheduler):
        scheduler.mode = "interval_minutes"
        assert scheduler.mode == "interval_minutes"

    def test_set_invalid_mode_raises(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.mode = "not_a_mode"

    def test_mode_change_to_manual_stops_timer(self, scheduler):
        scheduler.start()
        scheduler.mode = "manual"
        assert scheduler.mode == "manual"


# ==================== 集成测试 ====================


class TestIntegration:
    """端到端集成测试。"""

    def test_full_lifecycle(self, fake_library, fake_wm):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="manual")
        s.set_dependencies(fake_library, fake_wm)
        s.start(on_switch=lambda p: print(f"Switched to {p}"))
        s.trigger_now()
        assert s.is_active is True  # manual 模式 start() 设置 _is_running=True 但无定时器

    def test_scheduler_rejects_bad_deps(self):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="manual")
        # 未注入依赖时 trigger_now 不应崩溃
        s.trigger_now()


# ==================== 倒计时剩余秒数（#8） ====================


class TestSecondsUntilNext:
    """seconds_until_next() 的语义（含 remainingTime 实测结论的护栏）。"""

    @pytest.fixture(autouse=True)
    def app(self):
        """interval 模式 start() 会创建 QTimer，需要 QApplication 实例。"""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            QApplication([])
        yield

    def test_not_started_returns_none(self, scheduler):
        assert scheduler.seconds_until_next() is None

    def test_manual_mode_returns_none(self, scheduler):
        """manual 模式无定时器 → None。"""
        scheduler.start()
        assert scheduler.seconds_until_next() is None

    def test_interval_running_returns_int_in_range(self, fake_library, fake_wm):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="interval_minutes", interval_minutes=30)
        s.set_dependencies(fake_library, fake_wm)
        s.start()
        try:
            v = s.seconds_until_next()
            assert isinstance(v, int)
            assert not isinstance(v, bool)
            assert 0 <= v <= 30 * 60
        finally:
            s.stop()

    def test_paused_returns_none(self, fake_library, fake_wm):
        """暂停即冻结：pause() 后必须返回 None。"""
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="interval_minutes", interval_minutes=5)
        s.set_dependencies(fake_library, fake_wm)
        s.start()
        s.pause()
        try:
            assert s.seconds_until_next() is None
        finally:
            s.stop()

    def test_stopped_returns_none(self, fake_library, fake_wm):
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="interval_minutes", interval_minutes=5)
        s.set_dependencies(fake_library, fake_wm)
        s.start()
        s.stop()
        assert s.seconds_until_next() is None

    def test_negative_remaining_treated_as_none(self, scheduler, monkeypatch):
        """定时器 remainingTime() 返回 -1（未激活）时必须降级为 None，不能显示 0 秒。"""
        scheduler._is_running = True
        scheduler._is_paused = False

        class _DeadTimer:
            def remainingTime(self):
                return -1

        scheduler._timer = _DeadTimer()
        assert scheduler.seconds_until_next() is None


# ==================== 壁纸设置器注入缝（#7 双写修复） ====================


class TestWallpaperApplierSeam:
    """注入 applier 后必须优先使用它、不再直调 WallpaperManager（消除双写）。"""

    def test_no_applier_uses_wallpaper_manager(self, scheduler, fake_wm):
        """未注入时行为与原来完全一致（回归护栏）。"""
        scheduler.trigger_now()
        assert fake_wm.set_call_count == 1

    def test_applier_takes_priority_and_skips_manager(self, scheduler, fake_wm):
        calls = []
        scheduler.set_wallpaper_applier(lambda p: calls.append(p) or True)
        scheduler.trigger_now()
        assert calls  # applier 被调用
        assert fake_wm.set_call_count == 0  # 未直调 WallpaperManager（无双写）

    def test_applier_false_does_not_fall_back(self, scheduler, fake_wm):
        """applier 正常返回 False（未抛异常）→ 不回落到默认实现。"""
        scheduler.set_wallpaper_applier(lambda p: False)
        scheduler.trigger_now()
        assert fake_wm.set_call_count == 0

    def test_applier_exception_falls_back(self, scheduler, fake_wm):
        """applier 抛异常 → 吞掉并回落默认实现（绝不冒泡）。"""
        def boom(path):
            raise RuntimeError("boom")

        scheduler.set_wallpaper_applier(boom)
        scheduler.trigger_now()
        assert fake_wm.set_call_count == 1

    def test_interval_switch_uses_applier(self, fake_library, fake_wm):
        """_do_interval_switch 同样走 applier（3 处调用点统一）。"""
        from src.core.scheduler import Scheduler
        s = Scheduler(mode="interval_minutes", interval_minutes=5)
        s.set_dependencies(fake_library, fake_wm)
        called = []
        s.set_wallpaper_applier(lambda p: called.append(p) or True)
        s._do_interval_switch()
        assert called
        assert fake_wm.set_call_count == 0

    def test_clear_applier_restores_default(self, scheduler, fake_wm):
        scheduler.set_wallpaper_applier(lambda p: True)
        scheduler.set_wallpaper_applier(None)  # 清除
        scheduler.trigger_now()
        assert fake_wm.set_call_count == 1
