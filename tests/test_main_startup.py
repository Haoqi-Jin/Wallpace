"""tests/test_main_startup.py — 启动期配置修正与调度器启动保护（P0-2）。

修复前：`switch_mode="interval_minutes"` + `interval_minutes=null` 时，
`main.py` 里裸调 `scheduler.start()` 抛 ValueError，打包成 --windowed 后
表现为"双击图标 → 无声退出"。这里直接覆盖 main.py 中新增的两个防护函数。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.core.scheduler import Scheduler  # noqa: E402


class FakeSettings:
    """最小 Settings 替身：只实现 get/set 语义。"""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class TestResolveIntervalMinutes:
    """启动时对 interval_minutes 的修正与落盘。"""

    def test_null_interval_in_interval_mode_is_fixed(self):
        from src.main import _resolve_interval_minutes

        settings = FakeSettings({"switch_mode": "interval_minutes", "interval_minutes": None})
        assert _resolve_interval_minutes(settings, "interval_minutes") == 60
        # 修正结果必须落盘，否则下次启动还会崩
        assert settings.get("interval_minutes") == 60

    def test_invalid_types_are_fixed(self):
        from src.main import _resolve_interval_minutes

        for bad in (0, -5, "30", 1.5, True):
            settings = FakeSettings(
                {"switch_mode": "interval_minutes", "interval_minutes": bad}
            )
            assert _resolve_interval_minutes(settings, "interval_minutes") == 60

    def test_valid_value_is_preserved(self):
        from src.main import _resolve_interval_minutes

        settings = FakeSettings(
            {"switch_mode": "interval_minutes", "interval_minutes": 30}
        )
        assert _resolve_interval_minutes(settings, "interval_minutes") == 30

    def test_non_interval_mode_is_untouched(self):
        from src.main import _resolve_interval_minutes

        settings = FakeSettings({"switch_mode": "manual", "interval_minutes": None})
        assert _resolve_interval_minutes(settings, "manual") is None
        assert settings.get("interval_minutes") is None


class TestStartScheduler:
    """调度器启动失败时降级为手动模式，绝不让主窗口起不来。"""

    def test_normal_start_returns_true(self):
        from src.main import start_scheduler

        scheduler = Scheduler(mode="manual")
        assert start_scheduler(scheduler, lambda _p: None) is True
        assert scheduler.is_running
        scheduler.stop()

    def test_invalid_interval_degrades_to_manual(self):
        """模拟"构造不崩但 start() 抛 ValueError"的历史配置。"""
        from src.main import start_scheduler

        scheduler = Scheduler(mode="interval_minutes", interval_minutes=None)
        assert start_scheduler(scheduler, lambda _p: None) is False
        # 已降级为手动模式，且仍处于可用（已启动）状态
        assert scheduler.mode == "manual"
        assert scheduler.is_running
        scheduler.stop()

    def test_start_never_raises_on_broken_config(self):
        from src.main import start_scheduler

        scheduler = Scheduler(mode="interval_minutes", interval_minutes=0)
        # 不得抛出：任何调度器异常都不应阻止主窗口显示
        start_scheduler(scheduler, lambda _p: None)
        scheduler.stop()
