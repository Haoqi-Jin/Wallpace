"""src/core/directory_watcher.py — 图片文件夹变更监听。

用户配置的图片文件夹内容发生变化（新增/删除/重命名图片）时，应用能自动感知
并触发一次重新扫描，无需手动点刷新。

为什么用 watchdog 而不是 QFileSystemWatcher
-------------------------------------------
`ImageLibrary.scan()` 用 `rglob` **递归**扫描子目录；而 Qt 自带的
`QFileSystemWatcher` 监听目录时**不递归子目录**，需要为每个子目录单独注册并
在结构变化时维护注册列表，成本高且易漏。`watchdog` 的 Observer 天然支持
递归监听（recursive=True）。

线程模型（硬性约束）
--------------------
`watchdog` 的 Observer 在**独立线程**回调事件。该线程里：
  * 绝不能操作任何 QWidget；
  * 绝不能直接调用 `library.scan()`（会与主线程/扫描线程竞争）。
本模块的做法是：Observer 线程只 emit 一个内部 Qt 信号，Qt 自动以
QueuedConnection 把事件排队回主线程；主线程里做防抖，防抖结束后才 emit 对外
的 `changes_detected` 信号。因此 `changes_detected` 的所有槽都运行在主线程，
可以安全操作 UI 与 library。

防抖
----
一次复制 100 张图会产生上百个事件；若每个事件都触发重扫会疯狂占用 IO。
这里把 `debounce_ms`（默认 1500 ms）内的所有事件合并为一次通知。
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from PySide6.QtCore import QObject, QTimer, Signal

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# 默认防抖窗口：1.5 秒内的事件合并为一次重新扫描
DEFAULT_DEBOUNCE_MS = 1500

# Observer.stop() 后等待线程退出的超时（秒）
_OBSERVER_JOIN_TIMEOUT = 3.0


class _ImageChangeHandler(FileSystemEventHandler):
    """watchdog 事件处理器，运行在 Observer 线程。

    只做「判断事件是否值得关注 → 发信号」两件事，绝不触碰 QWidget，
    也不直接调用 library.scan()。
    """

    def __init__(
        self,
        on_relevant_event: Callable[[], None],
        get_extensions: Callable[[], Set[str]],
    ) -> None:
        super().__init__()
        self._on_relevant_event = on_relevant_event
        self._get_extensions = get_extensions

    def on_any_event(self, event: Any) -> None:
        """watchdog 的统一入口（创建/删除/修改/移动都会走这里）。"""
        try:
            if self._is_relevant(event):
                self._on_relevant_event()
        except RuntimeError:
            # 持有信号的 QObject 已被销毁，忽略
            pass
        except Exception:
            logger.exception("处理文件变更事件失败")

    def _is_relevant(self, event: Any) -> bool:
        """判断事件是否需要触发重新扫描。

        * 目录本身的增删/重命名会影响递归扫描结果 → 一律关注；
        * 普通文件只关注扩展名在配置里的图片文件（忽略 .txt/.db 等噪音）。
        """
        if getattr(event, "is_directory", False):
            return True
        for attr in ("src_path", "dest_path"):
            raw = getattr(event, attr, "")
            if raw and self._matches_extension(str(raw)):
                return True
        return False

    def _matches_extension(self, path_str: str) -> bool:
        suffix = Path(path_str).suffix.lstrip(".").lower()
        if not suffix:
            return False
        return suffix in self._get_extensions()


class DirectoryWatcher(QObject):
    """递归监听若干图片文件夹，变更经防抖后以 Qt 信号通知（主线程）。

    典型接线::

        watcher = DirectoryWatcher(extensions=["jpg", "png"], parent=main_window)
        watcher.changes_detected.connect(main_window.on_dirs_changed)
        watcher.set_directories(settings.get("image_directories", []))
        # 退出时
        watcher.stop()
    """

    # 防抖结束后发出（主线程）。槽里可以安全操作 UI / library。
    changes_detected = Signal()

    # 内部信号：Observer 线程 emit，Qt 自动以 QueuedConnection 排回主线程。
    # 必须是类属性 —— PySide6 的信号由 QObject 元类在类创建时处理，
    # 实例属性上的 Signal() 不会成为真正可用的信号。
    _raw_event = Signal()

    def __init__(
        self,
        extensions: Optional[Iterable[str]] = None,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        observer_factory: Optional[Callable[[], Any]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._extensions: Set[str] = self._normalize_extensions(extensions)
        self._debounce_ms: int = int(debounce_ms)
        self._observer_factory: Callable[[], Any] = observer_factory or Observer

        self._enabled: bool = True
        self._directories: List[str] = []
        self._watches: Dict[str, Any] = {}  # 归一化目录路径 -> ObservedWatch
        self._observer: Optional[Any] = None

        self._handler = _ImageChangeHandler(
            on_relevant_event=self._on_relevant_event,
            get_extensions=lambda: self._extensions,
        )

        # 内部信号：Observer 线程 emit，Qt 自动排队回主线程
        self._raw_event.connect(self._on_raw_event)

        # 防抖定时器（主线程，父对象为本 QObject，随窗口一起销毁）
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._flush)

    # ==================== 公开 API ====================

    @property
    def is_enabled(self) -> bool:
        """是否处于启用状态（关闭时不会启动任何 Observer）。"""
        return self._enabled

    @property
    def is_running(self) -> bool:
        """Observer 线程是否正在运行。"""
        return self._observer is not None

    @property
    def watched_directories(self) -> List[str]:
        """当前实际被监听的目录（归一化后的路径）。"""
        return sorted(self._watches)

    @property
    def debounce_ms(self) -> int:
        """防抖窗口（毫秒）。"""
        return self._debounce_ms

    def set_extensions(self, extensions: Optional[Iterable[str]]) -> None:
        """更新关注的扩展名集合（不含点，小写）。"""
        self._extensions = self._normalize_extensions(extensions)

    def set_debounce_ms(self, debounce_ms: int) -> None:
        """更新防抖窗口（毫秒）。"""
        self._debounce_ms = max(0, int(debounce_ms))

    def set_enabled(self, enabled: bool) -> None:
        """开启/关闭监听。关闭时会停止 Observer 并清空所有 watch。"""
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._sync()
        else:
            self._teardown_observer()

    def set_directories(self, directories: Optional[Iterable[str]]) -> None:
        """同步监听的目录列表（增量增删，不会重建已有 watch）。

        Args:
            directories: 目录路径列表；不存在的目录会被忽略。
        """
        self._directories = [str(d) for d in (directories or [])]
        self._sync()

    def start(self) -> None:
        """按当前目录列表启动监听（幂等）。"""
        self._sync()

    def stop(self) -> None:
        """停止 Observer 并等待线程退出（幂等，可在退出路径重复调用）。"""
        self._teardown_observer()
        self._debounce_timer.stop()

    def notify_manual(self) -> None:
        """手动投递一次变更通知（等价于 Observer 线程发来一个相关事件）。"""
        self._on_relevant_event()

    # ==================== 内部实现 ====================

    @staticmethod
    def _normalize_extensions(extensions: Optional[Iterable[str]]) -> Set[str]:
        """把扩展名统一成不含点的小写集合。"""
        if not extensions:
            return set()
        return {
            str(e).lstrip(".").lower()
            for e in extensions
            if str(e).strip()
        }

    @staticmethod
    def _key(directory: str) -> str:
        """目录的归一化键（Windows 下路径大小写/分隔符可能不一致）。"""
        try:
            return str(Path(directory).resolve())
        except OSError:
            return str(Path(directory))

    def _on_relevant_event(self) -> None:
        """Observer 线程调用：只 emit 内部信号，由 Qt 排回主线程。"""
        try:
            self._raw_event.emit()
        except RuntimeError:
            pass

    def _on_raw_event(self) -> None:
        """主线程：重启防抖定时器，把窗口期内的事件合并为一次。"""
        if self._debounce_ms <= 0:
            self._flush()
            return
        self._debounce_timer.start(self._debounce_ms)

    def _flush(self) -> None:
        """主线程：防抖窗口结束，对外发出变更通知。"""
        self._debounce_timer.stop()
        self.changes_detected.emit()

    def _ensure_observer(self) -> Optional[Any]:
        """惰性创建并启动 Observer（没有任何目录时不创建，避免空转线程）。"""
        if self._observer is not None:
            return self._observer
        try:
            observer = self._observer_factory()
            observer.start()
        except Exception:
            logger.exception("启动文件监听 Observer 失败")
            return None
        self._observer = observer
        return self._observer

    def _teardown_observer(self) -> None:
        """停止并丢弃 Observer，清空 watch 表。"""
        self._debounce_timer.stop()
        observer = self._observer
        self._observer = None
        self._watches = {}
        if observer is None:
            return
        try:
            observer.stop()
        except Exception:
            logger.warning("停止文件监听 Observer 失败", exc_info=True)
        try:
            observer.join(_OBSERVER_JOIN_TIMEOUT)
        except Exception:
            logger.warning("等待文件监听 Observer 退出失败", exc_info=True)

    def _sync(self) -> None:
        """把 _directories 同步到 watchdog 的 watch 表（增量）。"""
        if not self._enabled:
            if self._observer is not None:
                self._teardown_observer()
            return

        wanted: Dict[str, str] = {}
        for directory in self._directories:
            path = Path(directory)
            if not path.is_dir():
                logger.debug("跳过无效监听目录: %s", directory)
                continue
            wanted[self._key(directory)] = str(path)

        if not wanted:
            # 没有有效目录：不留空转线程
            if self._observer is not None:
                self._teardown_observer()
            return

        observer = self._ensure_observer()
        if observer is None:
            return

        # 1) 移除不再需要的 watch
        for key in list(self._watches):
            if key not in wanted:
                watch = self._watches.pop(key)
                try:
                    observer.unschedule(watch)
                except Exception:
                    logger.warning("取消监听失败: %s", key, exc_info=True)

        # 2) 新增缺失的 watch
        for key, directory in wanted.items():
            if key in self._watches:
                continue
            try:
                self._watches[key] = observer.schedule(
                    self._handler, directory, recursive=True
                )
            except Exception:
                logger.warning("注册监听失败: %s", directory, exc_info=True)
