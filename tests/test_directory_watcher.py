"""tests/test_directory_watcher.py — 图片文件夹监听（DirectoryWatcher）。

用 observer_factory 注入 fake Observer，不真正启动 watchdog 后台线程，
从而稳定、无残留地覆盖：扩展名过滤、目录增量增删、防抖合并、启用开关。
"""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.directory_watcher import (  # noqa: E402
    DirectoryWatcher,
    _ImageChangeHandler,
)


class FakeWatch:
    """记录一次 schedule 返回的 watch 句柄。"""

    def __init__(self, path: str):
        self.path = path


class FakeObserver:
    """代替 watchdog.Observer 的纯内存替身，不启动任何线程。"""

    def __init__(self):
        self.scheduled = []      # (handler, path, recursive)
        self.unscheduled = []    # watch
        self.started = False
        self.stopped = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout=None) -> None:
        self.joined = True

    def schedule(self, handler, path, recursive=True):
        watch = FakeWatch(path)
        self.scheduled.append((handler, path, recursive))
        return watch

    def unschedule(self, watch) -> None:
        self.unscheduled.append(watch)


@pytest.fixture(scope="module")
def app():
    if not QApplication.instance():
        QApplication([])
    yield QApplication.instance()


@pytest.fixture
def make_watcher(app):
    """返回 (factory, created_observers) 工厂闭包，便于断言 observer 生命周期。"""
    created = []

    def factory():
        obs = FakeObserver()
        created.append(obs)
        return obs

    def _make(**kwargs):
        kwargs.setdefault("observer_factory", factory)
        return DirectoryWatcher(**kwargs)

    return _make, created


def _wait_ms(ms: float) -> None:
    """推进事件循环并等待若干毫秒。"""
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


class TestNormalizeExtensions:
    def test_strips_dots_and_lowercases(self):
        assert DirectoryWatcher._normalize_extensions(
            [".JPG", "png", ".webp"]
        ) == {"jpg", "png", "webp"}

    def test_empty_returns_empty(self):
        assert DirectoryWatcher._normalize_extensions(None) == set()
        assert DirectoryWatcher._normalize_extensions([]) == set()


class TestIsRelevant:
    def _handler(self, extensions):
        return _ImageChangeHandler(
            on_relevant_event=lambda: None,
            get_extensions=lambda: set(extensions),
        )

    def test_image_extension_is_relevant(self):
        h = self._handler({"jpg", "png"})
        assert h._is_relevant(SimpleNamespace(src_path="a.jpg", is_directory=False)) is True
        assert h._is_relevant(SimpleNamespace(dest_path="b.png", is_directory=False)) is True

    def test_non_image_extension_is_ignored(self):
        h = self._handler({"jpg"})
        assert h._is_relevant(SimpleNamespace(src_path="a.txt", is_directory=False)) is False
        assert h._is_relevant(SimpleNamespace(src_path="a", is_directory=False)) is False

    def test_directory_event_is_always_relevant(self):
        h = self._handler(set())
        assert h._is_relevant(SimpleNamespace(src_path="some/dir", is_directory=True)) is True

    def test_case_and_dot_insensitive(self):
        h = self._handler({"jpg"})
        assert h._is_relevant(SimpleNamespace(src_path="A.JPG", is_directory=False)) is True
        assert h._is_relevant(SimpleNamespace(src_path="a.jpeg", is_directory=False)) is False


class TestSetDirectories:
    def _dirs(self, tmp_path, *names):
        out = []
        for name in names:
            d = tmp_path / name
            d.mkdir(exist_ok=True)
            out.append(str(d))
        return out

    def test_schedule_adds_watches_incrementally(self, app, tmp_path, make_watcher):
        make, created = make_watcher
        d1, d2 = self._dirs(tmp_path, "d1", "d2")

        w = make()
        w.set_directories([d1, d2])
        assert len(created) == 1 and created[0].started is True
        assert set(w.watched_directories) == {str(Path(d1).resolve()), str(Path(d2).resolve())}

        # 减到一个：应 unschedule 掉 d2
        w.set_directories([d1])
        assert set(w.watched_directories) == {str(Path(d1).resolve())}
        assert any(str(Path(d2).resolve()) == wk.path for wk in created[0].unscheduled)
        w.stop()

    def test_empty_directories_never_starts_observer(self, app, make_watcher):
        make, created = make_watcher
        w = make()
        w.set_directories([])
        assert created == []
        assert w.is_running is False
        assert w.watched_directories == []

    def test_invalid_directories_are_ignored(self, app, tmp_path, make_watcher):
        make, created = make_watcher
        w = make()
        w.set_directories([str(tmp_path / "does_not_exist")])
        assert created == []
        assert w.watched_directories == []

    def test_removing_all_directories_stops_observer(self, app, tmp_path, make_watcher):
        make, created = make_watcher
        d1 = self._dirs(tmp_path, "d1")[0]
        w = make()
        w.set_directories([d1])
        assert w.is_running is True
        w.set_directories([])
        assert w.is_running is False
        assert created[0].stopped is True


class TestEnableToggle:
    def test_disable_tears_down_and_re_enable_resyncs(self, app, tmp_path, make_watcher):
        make, created = make_watcher
        d1 = tmp_path / "d1"
        d1.mkdir()

        w = make()
        w.set_directories([str(d1)])
        assert w.is_running is True

        w.set_enabled(False)
        assert w.is_running is False
        assert w.watched_directories == []
        assert created[0].stopped is True

        # 重新启用：重新建 observer 并注册目录
        w.set_enabled(True)
        assert w.is_running is True
        assert set(w.watched_directories) == {str(Path(d1).resolve())}
        w.stop()


class TestDebounce:
    def test_zero_debounce_flushes_immediately(self, app, make_watcher):
        make, _ = make_watcher
        w = make(debounce_ms=0)
        fired = []
        w.changes_detected.connect(lambda: fired.append(1))
        w.notify_manual()
        QApplication.processEvents()
        assert fired == [1]

    def test_events_within_window_are_merged(self, app, make_watcher):
        make, _ = make_watcher
        w = make(debounce_ms=120)
        fired = []
        w.changes_detected.connect(lambda: fired.append(1))

        for _ in range(5):
            w.notify_manual()
        _wait_ms(40)  # 仍在防抖窗口内
        assert fired == []

        _wait_ms(200)  # 越过窗口后只应触发一次
        assert fired == [1]

    def test_stop_clears_pending_debounce(self, app, make_watcher):
        make, _ = make_watcher
        w = make(debounce_ms=500)
        fired = []
        w.changes_detected.connect(lambda: fired.append(1))
        w.notify_manual()
        w.stop()
        _wait_ms(600)
        assert fired == []
