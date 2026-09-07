"""src/app/pages/gallery_page.py — 图片库页面。

提供「全部 / 收藏 / 已跳过」筛选 + 缩略图网格。每张缩略图可：
  - 点击大图：设为当前壁纸
  - ★ 按钮：收藏 / 取消收藏
  - 恢复按钮（仅已跳过项显示）：从跳过列表中移除
收藏与跳过变更通过 on_persist 回调写回配置，避免重启后丢失。

缩略图解码走 src.app.image_loader 的按 path 订阅异步加载（后台线程解码，
回调只发给订阅该 path 的对象），不在主线程同步解码，也不在非主线程操作
任何 QWidget。

性能（P0-3）
------------
早期实现在 refresh() 中为**全部**图片同步创建 _Tile（每个约 6 个 QObject），
800 张图实测 7.8 秒主线程假死，且每次切到本页（showEvent）都完整重建。
现改为：
  1. 分页懒加载：首屏只创建 THUMB_BATCH 个，滚动接近底部时追加下一批；
  2. refresh() 去重：数据签名（筛选 + 路径列表 + 收藏/跳过数量）未变则直接
     返回，showEvent 每次切页不再无谓重建。
"""

import logging
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QPushButton,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app import image_loader

logger = logging.getLogger(__name__)

THUMB_SIZE = QSize(160, 120)
GRID_COLS = 4

# 分页懒加载：首屏一次性创建的 tile 数 + 距底部多少像素时预取下一批
THUMB_BATCH = 50
LOAD_AHEAD_PX = 320


class _Tile(QWidget):
    """单张缩略图卡片：缩略图 + 收藏/恢复操作。"""

    def __init__(
        self,
        path: str,
        library,
        on_set: Callable[[str], None],
        on_persist: Callable[[], None],
        page: "GalleryPage",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._library = library
        self._on_set = on_set
        self._on_persist = on_persist
        self._page = page

        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet(
            "QLabel { background: #f3f4f6; border-radius: 6px; }"
            "QLabel:hover { border: 2px solid #ec4899; }"
        )
        self._thumb.mousePressEvent = lambda _e: self._on_set(self._path)

        self._fav_btn = QPushButton("★" if library.is_favorite(path) else "☆")
        self._fav_btn.setFixedSize(28, 28)
        self._fav_btn.setStyleSheet("QPushButton { border: none; font-size: 16px; }")
        self._fav_btn.clicked.connect(self._toggle_fav)

        self._skip_btn = QPushButton("恢复")
        self._skip_btn.setFixedSize(40, 28)
        self._skip_btn.setStyleSheet(
            "QPushButton { border: 1px solid #ffcdd2; border-radius: 4px;"
            " color: #c62828; font-size: 11px; }"
        )
        self._skip_btn.setVisible(self._path in library.skip_list)
        self._skip_btn.clicked.connect(self._unskip)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addWidget(self._fav_btn)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch(1)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)
        vbox.addWidget(self._thumb)
        vbox.addLayout(btn_row)

        # 按 path 订阅异步解码结果（后台线程解码，回调只发给本 tile）
        image_loader.load_async(path, THUMB_SIZE, self._on_image_ready)

    def _on_image_ready(self, path: str, image: QImage) -> None:
        if path != self._path:
            return
        try:
            pix = QPixmap.fromImage(image)
            self._thumb.setPixmap(
                pix.scaled(
                    THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except RuntimeError:
            # widget 已被销毁，忽略
            pass

    def _toggle_fav(self) -> None:
        if self._library.is_favorite(self._path):
            self._library.unfavorite(self._path)
        else:
            self._library.favorite(self._path)
        self._fav_btn.setText("★" if self._library.is_favorite(self._path) else "☆")
        self._on_persist()

    def _unskip(self) -> None:
        self._library.unskip(self._path)
        self._on_persist()
        # 当前已不在跳过列表，刷新页面以移除该卡片
        self._page.refresh()

    def unsubscribe(self) -> None:
        """退订该 tile 的图片解码回调（销毁前调用，避免野回调）。"""
        image_loader.cancel(self._path, self._on_image_ready)


class GalleryPage(QWidget):
    """图片库页面：全部 / 收藏 / 已跳过 筛选 + 缩略图网格（分页懒加载）。"""

    def __init__(
        self,
        library,
        image_loader,
        on_set_wallpaper: Callable[[str], None],
        on_persist: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._image_loader = image_loader
        self._on_set_wallpaper = on_set_wallpaper
        self._on_persist = on_persist
        self._filter = "all"  # all | favorites | skipped

        # --- 分页懒加载状态 ---
        self._pending_paths: List[str] = []   # 尚未创建 tile 的图片路径
        self._rendered_count: int = 0         # 已创建 tile 的数量（也是下一个网格下标）
        self._signature: Optional[Tuple] = None  # 上次渲染的数据签名，用于去重

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)

        title = QLabel("图片库")
        title.setObjectName("title")
        layout.addWidget(title)

        # 筛选按钮行
        filter_row = QHBoxLayout()
        self._btn_all = QPushButton("全部")
        self._btn_fav = QPushButton("收藏")
        self._btn_skip = QPushButton("已跳过")
        for btn, key in (
            (self._btn_all, "all"),
            (self._btn_fav, "favorites"),
            (self._btn_skip, "skipped"),
        ):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=key: self._set_filter(k))
            filter_row.addWidget(btn)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        # 滚动网格
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._grid = QGridLayout(self._content)
        self._grid.setSpacing(10)
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, stretch=1)

        # 滚动接近底部时追加下一批（懒加载）
        vbar = self._scroll.verticalScrollBar()
        vbar.valueChanged.connect(self._maybe_append_batch)
        vbar.rangeChanged.connect(lambda _min, _max: self._maybe_append_batch())

        self._update_filter_buttons()
        self.refresh()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._update_filter_buttons()
        self.refresh()

    def _update_filter_buttons(self) -> None:
        self._btn_all.setChecked(self._filter == "all")
        self._btn_fav.setChecked(self._filter == "favorites")
        self._btn_skip.setChecked(self._filter == "skipped")

    def _current_paths(self) -> List[str]:
        if self._filter == "favorites":
            return self._library.favorites
        if self._filter == "skipped":
            return self._library.skip_list
        return self._library.list_available()

    def _data_signature(self, paths: List[str]) -> Tuple:
        """当前渲染数据的签名，用于判断"数据是否真的变了"。

        包含收藏/跳过数量，因为切换「全部」筛选下的收藏状态时路径列表不变，
        但 tile 上的 ★ 需要重绘。
        """
        return (
            self._filter,
            tuple(paths),
            len(self._library.favorites),
            len(self._library.skip_list),
        )

    def refresh(self, force: bool = False) -> None:
        """根据当前筛选重建缩略图网格（首屏只建 THUMB_BATCH 个）。

        Args:
            force: True 时跳过数据签名比对，强制重建。
        """
        paths = self._current_paths()
        signature = self._data_signature(paths)
        if not force and signature == self._signature:
            # 数据未变更，跳过重建（showEvent 每次切页都会调用 refresh）
            return
        self._signature = signature

        self._clear_grid()

        self._pending_paths = list(paths)
        self._rendered_count = 0
        self._append_batch()

    def _clear_grid(self) -> None:
        """清空网格：先从布局摘除 item，再退订 + deleteLater。

        注意：必须用 takeAt 先把 item 从布局中移除再 deleteLater，否则
        count() 不会减少（销毁要等事件循环），会死循环。
        """
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is None:
                continue
            if isinstance(widget, _Tile):
                widget.unsubscribe()
            widget.setParent(None)
            widget.deleteLater()

    def _append_batch(self) -> None:
        """从待渲染队列取一批图片创建 tile（主线程）。"""
        if not self._pending_paths:
            return
        batch = self._pending_paths[:THUMB_BATCH]
        del self._pending_paths[: len(batch)]
        for path in batch:
            tile = _Tile(
                path,
                self._library,
                self._on_set_wallpaper,
                self._on_persist,
                self,
            )
            index = self._rendered_count
            self._grid.addWidget(tile, index // GRID_COLS, index % GRID_COLS)
            self._rendered_count += 1

    def _maybe_append_batch(self, *args) -> None:  # noqa: ANN002
        """滚动接近底部时追加下一批缩略图（懒加载）。"""
        if not self._pending_paths:
            return
        vbar = self._scroll.verticalScrollBar()
        if vbar.maximum() - vbar.value() <= LOAD_AHEAD_PX:
            self._append_batch()

    def load_all(self) -> None:
        """立即创建剩余全部 tile（供测试/导出场景使用）。"""
        while self._pending_paths:
            self._append_batch()

    @property
    def rendered_count(self) -> int:
        """当前已创建的 tile 数量。"""
        return self._rendered_count

    @property
    def pending_count(self) -> int:
        """尚未创建的 tile 数量。"""
        return len(self._pending_paths)

    def showEvent(self, event) -> None:  # noqa: ANN001
        # 切到该页时按最新 library 状态刷新；数据未变则 refresh 内部直接返回
        self.refresh()
        super().showEvent(event)
