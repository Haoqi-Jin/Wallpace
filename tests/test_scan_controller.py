"""tests/test_scan_controller.py — ScanController 独立单测（#1 搬迁）。

脱离 MainWindow 验证扫描状态机：started/finished 信号、running/pending 合并补扫、
shutdown 幂等。使用 offscreen 平台与假 ImageLibrary，不依赖真实文件。
"""

import os
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class _FakeLibrary:
    """最小 ImageLibrary 替身：只实现后台扫描依赖的 scan()。"""

    def __init__(self, result=None):
        self.result = list(result) if result is not None else ["a", "b"]
        self.scan_calls = 0

    def scan(self):
        self.scan_calls += 1
        return list(self.result)


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not QApplication.instance():
        QApplication([])
    yield


def _wait_until(predicate, timeout: float = 5.0) -> None:
    """驱动事件循环直到 predicate 为真（投递后台任务的完成信号）。"""
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


@pytest.fixture
def controller(app):
    from src.app.scan_controller import ScanController

    lib = _FakeLibrary(["x", "y", "z"])
    ctrl = ScanController(lib)
    yield ctrl, lib
    ctrl.shutdown()


class TestScanController:
    """ScanController 状态机行为。"""

    def test_starts_idle(self, controller):
        ctrl, _ = controller
        assert ctrl.running is False
        assert ctrl.pending is False

    def test_request_scan_emits_started_then_finished(self, controller):
        ctrl, lib = controller
        started, finished = [], []
        ctrl.started.connect(lambda: started.append(1))
        ctrl.finished.connect(lambda imgs: finished.append(list(imgs)))

        ctrl.request_scan()
        assert ctrl.running is True
        assert started == [1]  # started 同步发出

        _wait_until(lambda: finished)
        assert finished == [["x", "y", "z"]]
        assert lib.scan_calls == 1

    def test_while_running_merges_to_pending(self, controller):
        ctrl, lib = controller
        finished = []
        ctrl.finished.connect(lambda imgs: finished.append(1))
        ctrl.running = True  # 模拟已有在途扫描

        ctrl.request_scan()  # 防重入：应标记补扫而非并发

        assert ctrl.pending is True
        assert lib.scan_calls == 0
        assert finished == []

    def test_running_and_pending_setters(self, controller):
        ctrl, _ = controller
        ctrl.running = True
        ctrl.pending = True
        assert ctrl.running is True
        assert ctrl.pending is True
        ctrl.running = False
        ctrl.pending = False
        assert ctrl.running is False
        assert ctrl.pending is False

    def test_shutdown_is_idempotent(self, controller):
        ctrl, _ = controller
        ctrl.shutdown()
        ctrl.shutdown()  # 重复调用不得抛异常
        assert ctrl.running is False
