"""src/app/widgets/gallery_strip.py — 横向缩略图条（#1 从 window.py 抽出）。

封装两个内聚单元：
- ``GalleryThumbWidget``：可点击缩略图控件，后台异步解码 ``QImage`` → ``QPixmap``；
- ``GalleryStrip``：横向滚动区 + 懒加载状态机（首批 ``THUMB_BATCH`` 张，
  滚动接近右端再追加下一批）。

缩略图经 ``image_loader`` 在后台线程解码，避免主线程加载 4K 大图造成卡顿。
设计见 ``docs/optimization-batch2-design.md`` §1.4。
"""

import logging
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app import image_loader

logger = logging.getLogger(__name__)


class GalleryThumbWidget(QLabel):
    """可点击的图片库缩略图，支持高亮当前选中项。

    缩略图通过 image_loader 在后台线程解码，避免在主线程加载大图造成卡顿。
    """

    clicked = Signal(str)  # emits image path on click
    _THUMB_SIZE = QSize(200, 160)

    def __init__(
        self,
        image_path: str,
        is_current: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_path = image_path
        self._is_current = is_current
        self.setFixedSize(80, 60)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style()
        # 异步加载缩略图：按 path 订阅，解码完成只回调本 widget（无需 disconnect）
        image_loader.load_async(image_path, self._THUMB_SIZE, self._on_image_ready)

    def _on_image_ready(self, path: str, image) -> None:
        """后台解码完成的回调（主线程执行）。只处理匹配当前 path 的图片。"""
        if path != self._image_path:
            return
        if image.isNull():
            return
        try:
            scaled = QPixmap.fromImage(image).scaled(
                80,
                60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(scaled)
        except RuntimeError:
            # widget 已被销毁，忽略
            pass

    def _apply_style(self) -> None:
        border_color = "#ec4899" if self._is_current else "#eeeeee"
        self.setStyleSheet(
            "QLabel { "
            f"border-radius: 6px; background-color: #f5f5f5; "
            f"border: 2px solid {border_color}; "
            "}"
        )

    def set_is_current(self, current: bool) -> None:
        self._is_current = current
        self._apply_style()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._image_path)
        super().mousePressEvent(event)


class GalleryStrip(QWidget):
    """横向缩略图条：自带滚动区 + 懒加载状态机。

    对外语义与搬迁前的 MainWindow 内部实现保持一致：

    - ``reload(paths, current_path)``：清空重建，只立即加载首批，其余懒加载；
    - ``set_current(path)``：高亮当前项、取消其余项高亮；
    - ``layout()``：返回内部缩略图 ``QHBoxLayout``（供 ``MainWindow._gallery_layout``
      读取 ``count()``）。
    """

    THUMB_BATCH = 10
    LOAD_AHEAD_PX = 240

    def __init__(
        self,
        on_select: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_select = on_select
        self._pending: list = []  # 尚未创建 widget 的图片路径
        self._items: list = []    # 已加载（已创建 widget）的图片路径
        self._current_path: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setFixedHeight(100)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setWidgetResizable(True)

        self._content = QWidget()
        self._thumbs_layout = QHBoxLayout(self._content)
        self._thumbs_layout.setContentsMargins(0, 0, 0, 0)
        self._thumbs_layout.setSpacing(8)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)

        # 懒加载：滚动接近右端（或内容区范围变化）时按需追加下一批缩略图
        hbar = self._scroll.horizontalScrollBar()
        hbar.valueChanged.connect(self._maybe_load_more)
        hbar.rangeChanged.connect(lambda _min, _max: self._maybe_load_more())

    # ----- facade -----

    def layout(self) -> QHBoxLayout:  # type: ignore[override]
        """返回内部缩略图 ``QHBoxLayout``（供 MainWindow._gallery_layout 读 count）。

        注意：``QWidget.layout()`` 在 Qt 中**非虚**，此处仅遮蔽 Python 侧调用，
        Qt 自身的几何管理仍使用本控件真实的外层布局，二者互不影响。
        """
        return self._thumbs_layout

    # ----- 重建 / 高亮 -----

    def reload(self, paths: list, current_path: str = "") -> None:
        """重建缩略图区域：只立即加载首批，其余滚动到时懒加载。

        清理旧 widget 时先用 ``takeAt`` 把 item 从布局中摘除，再 cancel 订阅 +
        ``deleteLater``。退订走 ``image_loader.cancel()``，不依赖
        ``QMetaObject.Connection.disconnect()``（本机 PySide6 无该方法，见 P0-1）。
        """
        while self._thumbs_layout.count():
            item = self._thumbs_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is None:
                continue
            if isinstance(widget, GalleryThumbWidget):
                image_loader.cancel(widget._image_path, widget._on_image_ready)
            widget.setParent(None)
            widget.deleteLater()

        self._pending = list(paths)
        self._items = []
        self._current_path = current_path
        self._scroll.horizontalScrollBar().setValue(0)
        self._append_batch()  # 首批 THUMB_BATCH 张

    def set_current(self, path: str) -> None:
        """高亮当前选中项（其余取消高亮），并同步记录当前路径。

        ``_current_path`` 必须一并更新：懒加载的 ``_append_batch()`` 正是用它
        判定新批次缩略图的高亮（``is_current=(img_path == self._current_path)``）。
        历史上这里只改已存在的 widget、不回写该字段，于是"切换到 A → 滚动触发
        懒加载追加新批次"时，新批次仍按**陈旧路径**判定，导致同屏出现两个高亮项
        （QA 审计结论 1：慢加载批次高亮漂移）。
        """
        self._current_path = path
        for idx in range(self._thumbs_layout.count()):
            item = self._thumbs_layout.itemAt(idx)
            widget = item.widget() if item is not None else None
            if isinstance(widget, GalleryThumbWidget):
                widget.set_is_current(widget._image_path == path)

    # ----- 懒加载 -----

    def _append_batch(self) -> None:
        """从待加载队列取一批图片创建缩略图 widget（主线程）。"""
        batch = self._pending[: self.THUMB_BATCH]
        del self._pending[: len(batch)]
        if not batch:
            return
        for img_path in batch:
            thumb = GalleryThumbWidget(
                img_path, is_current=(img_path == self._current_path)
            )
            thumb.clicked.connect(lambda p=img_path: self._on_select(p))
            self._thumbs_layout.addWidget(thumb)
        self._items.extend(batch)

    @Slot()
    def _maybe_load_more(self, *args) -> None:
        """滚动接近右端时追加下一批缩略图（懒加载）。"""
        if not self._pending:
            return
        hbar = self._scroll.horizontalScrollBar()
        if hbar.maximum() - hbar.value() <= self.LOAD_AHEAD_PX:
            self._append_batch()
