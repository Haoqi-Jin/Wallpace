"""src/app/image_loader.py — 异步图片解码加载器。

图片解码（尤其是 4K 壁纸）耗时数百毫秒，若在 UI 主线程同步执行会导致界面
卡顿冻结。此模块使用全局 QThreadPool（限制并发 4 线程）在后台线程解码图片，
解码完成通过 Qt 信号安全切回主线程更新 UI。

分发方式（重要）
----------------
早期实现把所有消费者都接到**同一个模块级广播信号**上，每个消费者在槽里
自己判断 `if path != self._path: return`。当 N 张缩略图同时解码时，单次
emit 会扇出 N 次回调，整体是 O(N²) 次主线程回调（P1-4）。

现改为**按 path 分发的订阅表**：调用方在提交任务时直接传入自己的回调，
解码完成只回调该 path 的订阅者。这样：
  * 回调次数从 O(N²) 降为 O(N)；
  * widget 销毁时用 `cancel()` 精确退订即可，**不再依赖
    `QMetaObject.Connection.disconnect()`**——本机 PySide6 的 Connection
    没有该方法，旧实现会在生产环境抛 AttributeError（P0-1）。

推荐用法（按 path 订阅，无需 disconnect）::

    from src.app import image_loader
    image_loader.load_async(path, QSize(200, 160), self._on_image_ready)
    # widget 销毁前（可选但推荐）：
    image_loader.cancel(path, self._on_image_ready)

兼容用法（全局广播，保留给只需要"看一眼"的场景）::

    sub = image_loader.connect_ready(lambda path, image: ...)
    sub.disconnect()
"""

import logging
import threading
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

logger = logging.getLogger(__name__)

# 回调类型：Callable[[str, QImage], None]
_ReadyCallback = Callable[[str, "QImage"], None]


class _LoaderSignals(QObject):
    """后台解码完成后的广播信号载体（QObject 生命周期由模块级实例持有）。"""

    ready = Signal(str, object)  # (path, QImage)


class _BroadcastSubscription:
    """`connect_ready()` 的返回值，提供真实可用的 `disconnect()`。

    之所以不直接返回 `QMetaObject.Connection`：PySide6 的 Connection 在本机
    没有 `disconnect()` 方法，历史代码（及测试）用 no-op 补丁掩盖了这一点，
    导致 P0-1 长期潜伏。这里返回一个可退订的包装对象，行为与预期一致。
    """

    __slots__ = ("_slot", "_active")

    def __init__(self, slot: _ReadyCallback) -> None:
        self._slot: Optional[_ReadyCallback] = slot
        self._active: bool = True

    def disconnect(self) -> None:
        """从广播列表中移除该槽；重复调用是安全的（幂等）。"""
        if not self._active:
            return
        self._active = False
        _remove_broadcast(self._slot)
        self._slot = None

    @property
    def is_active(self) -> bool:
        """是否仍处于连接状态。"""
        return self._active


# 模块级单例
_signals: _LoaderSignals = _LoaderSignals()
_pool: Optional[QThreadPool] = None

# --- 按 path 分发的订阅表 ---
# path -> [callback, ...]。解码完成（或失败）后该 path 的条目会被整体清空，
# 即订阅是"一次性"的：同一个 path 再次 load_async 会重新登记。
_subscribers: Dict[str, List[_ReadyCallback]] = {}
_subscribers_lock: threading.Lock = threading.Lock()

# --- 兼容用的全局广播列表 ---
_broadcast: List[_ReadyCallback] = []
_broadcast_lock: threading.Lock = threading.Lock()


def _get_pool() -> QThreadPool:
    """返回应用级线程池，限制并发数避免同时解码过多大图。"""
    global _pool
    if _pool is None:
        _pool = QThreadPool.globalInstance()
        _pool.setMaxThreadCount(4)
    return _pool


def _remove_broadcast(slot: Optional[_ReadyCallback]) -> None:
    """从广播列表中移除槽（内部辅助，不存在时静默忽略）。"""
    if slot is None:
        return
    with _broadcast_lock:
        try:
            _broadcast.remove(slot)
        except ValueError:
            pass


def connect_ready(slot: _ReadyCallback) -> _BroadcastSubscription:
    """连接全局解码完成信号到槽函数（兼容接口）。

    新代码请优先使用 `load_async(path, size, on_ready)`——按 path 分发，
    回调次数是 O(N) 而非 O(N²)，且不需要 disconnect。

    Args:
        slot: 回调，签名 slot(path: str, image: QImage)。在主线程执行。

    Returns:
        订阅对象，可调用其 `disconnect()` 退订（幂等）。
    """
    with _broadcast_lock:
        _broadcast.append(slot)
    return _BroadcastSubscription(slot)


def cancel(path: str, on_ready: _ReadyCallback) -> None:
    """取消某个 path 上的指定回调订阅。

    widget 即将被销毁时调用，避免解码完成后回调到已删除的 C++ 对象
    （那会抛 RuntimeError）。未登记过该回调时静默忽略。

    Args:
        path: 当初 load_async 传入的图片路径。
        on_ready: 当初传入的同一个回调对象（绑定方法的相等性按
            (实例, 函数) 比较，因此 self._on_image_ready 可正确匹配）。
    """
    with _subscribers_lock:
        callbacks = _subscribers.get(path)
        if not callbacks:
            return
        try:
            callbacks.remove(on_ready)
        except ValueError:
            return
        if not callbacks:
            del _subscribers[path]


def cancel_path(path: str) -> None:
    """取消某个 path 上的全部回调订阅（谨慎使用，会影响其它订阅者）。"""
    with _subscribers_lock:
        _subscribers.pop(path, None)


def pending_count() -> int:
    """返回当前尚未投递的 path 订阅条目数（供测试/诊断用）。"""
    with _subscribers_lock:
        return len(_subscribers)


def _dispatch(path: str, image: "QImage") -> None:
    """把解码结果投递给该 path 的订阅者，再广播给全局监听者（主线程）。"""
    with _subscribers_lock:
        callbacks = _subscribers.pop(path, [])
    for callback in callbacks:
        try:
            callback(path, image)
        except RuntimeError:
            # 底层 C++ 对象已被销毁，忽略
            pass
        except Exception:
            logger.exception("图片就绪回调异常: %s", path)

    with _broadcast_lock:
        listeners = list(_broadcast)
    for listener in listeners:
        try:
            listener(path, image)
        except RuntimeError:
            pass
        except Exception:
            logger.exception("广播图片就绪回调异常: %s", path)


class _DecodeJob(QRunnable):
    """后台解码任务：读取图片 → 受限尺寸缩放 → 回主线程分发。"""

    def __init__(self, path: str, target_size: QSize) -> None:
        super().__init__()
        self._path = path
        self._target_size = target_size

    def run(self) -> None:
        try:
            reader = QImageReader(self._path)
            reader.setAutoTransform(True)
            if self._target_size.isValid():
                reader.setScaledSize(self._target_size)
            image = reader.read()
            if image.isNull():
                logger.warning(
                    "图片解码失败: %s (%s)", self._path, reader.errorString()
                )
                # 失败也要清理订阅表，避免条目无限堆积
                _discard(self._path)
                return
            # emit 会自动排队到主线程（_signals 生存在主线程）
            _signals.ready.emit(self._path, image)
        except RuntimeError:
            # 信号槽连接已断开或对象已销毁，静默忽略
            _discard(self._path)
        except Exception:
            _discard(self._path)
            logger.exception("后台解码异常: %s", self._path)


def _discard(path: str) -> None:
    """解码失败/异常时丢弃该 path 的未投递订阅（内部辅助）。"""
    with _subscribers_lock:
        _subscribers.pop(path, None)


def load_async(
    path: str,
    target_size: Optional[QSize] = None,
    on_ready: Optional[_ReadyCallback] = None,
) -> None:
    """异步解码图片，完成后只回调该 path 的订阅者（再广播给全局监听者）。

    Args:
        path: 图片文件绝对路径。
        target_size: 目标尺寸；None 表示原图。
        on_ready: 可选的按 path 回调，签名 on_ready(path, image)，在主线程执行。
            传入后无需 connect/disconnect。
    """
    if on_ready is not None:
        with _subscribers_lock:
            _subscribers.setdefault(path, []).append(on_ready)
    _get_pool().start(_DecodeJob(path, target_size or QSize(0, 0)))


def _on_decoded(path: str, image: object) -> None:
    """模块内部槽：接收广播信号后做统一分发。"""
    _dispatch(path, image)


_signals.ready.connect(_on_decoded)
