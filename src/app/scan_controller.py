"""src/app/scan_controller.py — 图片库后台扫描状态机（#1 从 window.py 抽出）。

职责边界：
- 专用单线程池（``QThreadPool``）保证任何时刻最多执行一个扫描任务，避免并发 scan；
- ``running``/``pending`` 状态机把"扫描期间又来的刷新请求"合并为一次补扫；
- 通过 ``started``/``finished`` 信号把"状态变化 / 结果"回主线程，
  控制器本身**不含任何 UI 逻辑**（不触碰 QWidget），保持可脱离 MainWindow 单测。

设计见 ``docs/optimization-batch2-design.md`` §1.4。
"""

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

if TYPE_CHECKING:
    from src.core.image_library import ImageLibrary

logger = logging.getLogger(__name__)


class _ScanSignals(QObject):
    """后台扫描完成信号的载体（随 ScanController 生命周期）。"""

    finished = Signal(list)  # list[str] — 扫描得到的图片路径


class _ScanJob(QRunnable):
    """后台扫描任务：只调用 ImageLibrary.scan() 纯数据操作，绝不触碰 QWidget。

    扫描结果通过 Qt 信号以队列连接（QueuedConnection）回到主线程，
    由 ScanController._on_job_finished 在主线程转发给订阅者（MainWindow）。
    """

    def __init__(self, library: "ImageLibrary", signals: _ScanSignals) -> None:
        super().__init__()
        self._library = library
        self._signals = signals

    def run(self) -> None:
        try:
            images = self._library.scan()
        except Exception:
            logger.exception("后台扫描图片库失败")
            images = []
        try:
            self._signals.finished.emit(images)
        except RuntimeError:
            # 信号载体已随窗口销毁，静默丢弃结果
            pass


class ScanController(QObject):
    """图片库扫描状态机：把并发扫描请求合并为串行的补扫。

    Signals:
        started: 一次扫描开始（主线程）→ MainWindow 进入扫描态 UI。
        finished: 一次扫描完成（主线程），携带 ``list[str]`` 图片路径 →
            MainWindow 应用结果。**running 的复位由订阅者在 finished 处理中完成**，
            以保持与原 ``_on_scan_finished`` 完全一致的时序。
    """

    started = Signal()          # 扫描开始 → MainWindow._set_scan_ui_state(True)
    finished = Signal(list)     # 扫描完成 → MainWindow._on_scan_finished(list)

    def __init__(
        self, library: "ImageLibrary", parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._library = library
        # 专用单线程池：保证任何时刻最多一个扫描任务，避免并发 scan。
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _ScanSignals(self)
        self._signals.finished.connect(self._on_job_finished)
        self._running = False
        self._pending = False  # 扫描期间又有刷新请求时置位，完成后补一次

    # ----- 状态（可读写，供 MainWindow facade 兼容既有测试） -----

    @property
    def running(self) -> bool:
        """是否正在扫描。"""
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = bool(value)

    @property
    def pending(self) -> bool:
        """扫描期间是否收到过补扫请求。"""
        return self._pending

    @pending.setter
    def pending(self, value: bool) -> None:
        self._pending = bool(value)

    # ----- 对外操作 -----

    def request_scan(self) -> None:
        """投递一次后台扫描。扫描进行中再次调用时合并为一次补扫。"""
        if self._running:
            # 防重入：不并发多个 scan，标记待补扫即可
            self._pending = True
            return
        self._running = True
        self.started.emit()
        self._pool.start(_ScanJob(self._library, self._signals))

    @Slot(list)
    def _on_job_finished(self, images: list) -> None:
        """后台任务完成回调（主线程）：转发给订阅者。

        这里**不复位 running**：复位与补扫触发由 MainWindow._on_scan_finished
        完成，以保持与搬迁前完全一致的行为。
        """
        self.finished.emit(images)

    def shutdown(self, wait_ms: int = 1000) -> None:
        """清空队列并等待在途扫描结束，避免析构阻塞退出（幂等）。"""
        try:
            self._pool.clear()
            self._pool.waitForDone(wait_ms)
        except Exception:
            logger.exception("关闭扫描线程池失败")
