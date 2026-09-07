"""src/app/widgets/preview_card.py -- Wallpaper preview card component.

Shows current wallpaper thumbnail, filename, path + index overlay, 
and transition operations.
"""

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QImageReader, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import COLOR_GRAY_100, COLOR_GRAY_200, COLOR_GRAY_500
from src.app import image_loader

logger = logging.getLogger(__name__)


class PreviewCardWidget(QWidget):
    """Current wallpaper preview card component.

    Contains: large preview image with bottom gradient overlay +
    filename/source/path/index info, plus transition operations.
    """

    switch_clicked = Signal()
    skip_clicked = Signal()
    favorite_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_path: str = ""
        self._current_index: int = 0
        self._total_count: int = 0
        self._setup_ui()
        self._set_placeholder()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # === Preview image with overlay frame ===
        self.preview_frame = QWidget()
        self.preview_frame.setFixedHeight(340)
        self.preview_frame.setStyleSheet("background-color: transparent;")
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        # Image label (top portion)
        self.preview_label = QLabel("(预览图片)")
        self.preview_label.setObjectName("preview-image")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 340)
        self.preview_label.setMaximumSize(480, 340)
        preview_layout.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Bottom overlay (gradient + text info)
        self._setup_overlay()
        preview_layout.addWidget(self.overlay_frame, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self.preview_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # === Action buttons (mockup-style: large, centered, with icon) ===
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(12)

        btn_switch = QPushButton("换一张")
        btn_switch.clicked.connect(self.switch_clicked)
        btn_switch.setMinimumWidth(96)
        btn_switch.setStyleSheet(
            "QPushButton { "
            "border: none; border-radius: 10px; padding: 8px 24px; "
            "font-size: 14px; font-weight: 600; color: white; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #5c6bc0, stop:1 #e91e63); } "
            "QPushButton:hover { "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #7986cb, stop:1 #f06292); } "
            "QPushButton:pressed { "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #3f51b5, stop:1 #c2185b); }"
        )
        btn_layout.addWidget(btn_switch)

        btn_skip = QPushButton("跳过")
        btn_skip.clicked.connect(self.skip_clicked)
        btn_skip.setMinimumWidth(96)
        btn_skip.setStyleSheet(
            "QPushButton { "
            "border: none; border-radius: 10px; padding: 8px 24px; "
            "font-size: 14px; font-weight: 600; color: #424242; "
            "background: #f5f5f5; } "
            "QPushButton:hover { background: #eeeeee; }"
        )
        btn_layout.addWidget(btn_skip)

        btn_favorite = QPushButton("收藏")
        btn_favorite.clicked.connect(self.favorite_clicked)
        btn_favorite.setMinimumWidth(96)
        btn_favorite.setStyleSheet(
            "QPushButton { "
            "border: none; border-radius: 10px; padding: 8px 24px; "
            "font-size: 14px; font-weight: 600; color: #b45309; "
            "background: #fef3c7; } "
            "QPushButton:hover { background: #fde68a; }"
        )
        btn_layout.addWidget(btn_favorite)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _setup_overlay(self) -> None:
        """Bottom gradient overlay with filename, source path, and index number."""
        self.overlay_frame = QWidget()
        self.overlay_frame.setFixedHeight(80)
        overlay_layout = QVBoxLayout(self.overlay_frame)
        overlay_layout.setContentsMargins(0, 0, 0, 0)

        # Gradient background smooth fade from transparent to dark
        self.overlay_frame.setStyleSheet(
            "QWidget { "
            "background: qlineargradient("
            "x1:0, y1:0, x2:0, y2:1, "
            "stop:0 rgba(0,0,0,0), "
            "stop:0.5 rgba(0,0,0,0.2), "
            "stop:1 rgba(0,0,0,0.6)); "
            "border: none; }"
        )

        # File name label
        self.filename_label = QLabel("未选图片")
        self.filename_label.setObjectName("subtitle")
        self.filename_label.setStyleSheet("color: white; font-size: 14px; font-weight: 500;")
        overlay_layout.addWidget(self.filename_label)

        # Source path + index row
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 2, 0, 0)

        self.path_label = QLabel("")
        self.path_label.setObjectName("sub-title")
        self.path_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 11px;")
        info_row.addWidget(self.path_label)

        info_row.addStretch()

        self.index_label = QLabel("")
        self.index_label.setObjectName("index-badge")
        self.index_label.setStyleSheet(
            "QLabel#index-badge { "
            "color: white; font-size: 18px; font-weight: 700; "
            "background-color: rgba(255,255,255,0.15); "
            "padding: 4px 12px; border-radius: 8px; }"
        )
        info_row.addWidget(self.index_label)

        overlay_layout.addLayout(info_row)

    def _set_placeholder(self) -> None:
        self.preview_label.setStyleSheet(
            f"QLabel#preview-image {{ "
            f"border: 3px solid {COLOR_GRAY_200}; "
            f"background-color: {COLOR_GRAY_100}; "
            f"font-size: 14px; color: {COLOR_GRAY_500}; }} "
            f"min-width: 480px; min-height: 300px; "
            f"max-width: 480px; max-height: 300px;"
        )

    def update_preview(self, image_path: str, index: int = 0, total: int = 0) -> None:
        self._current_path = image_path
        self._current_index = index
        self._total_count = total

        file_name = Path(image_path).stem
        file_size = ""
        try:
            stat = Path(image_path).stat()
            size_kb = stat.st_size / 1024
            file_size = f"{size_kb:.0f} KB"
        except OSError:
            pass

        self.filename_label.setText(file_name)
        self.path_label.setText(str(Path(image_path).parent))
        self.index_label.setText(f"{index + 1} / {total}")

        # 显示等待占位，随后按 path 订阅异步解码结果（无需 connect/disconnect）
        self._set_loading()
        image_loader.load_async(image_path, QSize(900, 600), self._on_image_ready)

    # ===== 异步解码回调 =====

    def _on_image_ready(self, path: str, image) -> None:
        """后台解码完成后的回调（主线程执行），只处理当前预览图。"""
        if path != self._current_path:
            return
        try:
            if image.isNull():
                self._set_placeholder()
                return
            scaled = QPixmap.fromImage(image).scaled(
                480, 300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            self.preview_label.setStyleSheet(
                f"QLabel#preview-image {{ "
                f"border: 3px solid {COLOR_GRAY_200}; "
                f"border-radius: 12px; "
                f"background-color: transparent; "
                f"}}"
            )
        except RuntimeError:
            # widget 已被销毁，忽略
            pass

    def _set_loading(self) -> None:
        """显示加载中的占位提示。"""
        self.preview_label.clear()
        self.preview_label.setText("加载中…")
        self.preview_label.setStyleSheet(
            f"QLabel#preview-image {{ "
            f"border: 3px dashed {COLOR_GRAY_200}; "
            f"background-color: {COLOR_GRAY_100}; "
            f"font-size: 14px; color: {COLOR_GRAY_500}; }} "
            f"min-width: 480px; min-height: 300px; "
            f"max-width: 480px; max-height: 300px;"
        )

    def clear_preview(self) -> None:
        self._current_path = ""
        self._current_index = 0
        self._total_count = 0
        self.filename_label.setText("未选图片")
        self.path_label.setText("")
        self.index_label.setText("")
        self.preview_label.clear()
        self._set_placeholder()
