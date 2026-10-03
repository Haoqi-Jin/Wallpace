"""src/app/icon.py — 应用图标生成模块。

使用 PySide6 QPainter 绘制粉蓝渐变圆角矩形风格的 "WP" 字母图标。
不需要外部资源文件，可直接在 PyInstaller 打包后使用。
"""

import logging

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)

logger = logging.getLogger(__name__)


def create_app_icon(size: int = 256) -> QIcon:
    """创建一个粉蓝渐变风格的 Wallpace 应用图标（256x256）。

    Returns:
        QIcon 对象，可用于 QApplication.setWindowIcon() 和 QSystemTrayIcon.setIcon()。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 圆角矩形路径
    radius = size * 0.2
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, size, size),
        radius,
        radius,
    )

    # 粉蓝渐变
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#f472b6"))   # 粉色
    gradient.setColorAt(1.0, QColor("#818cf8"))   # 蓝色
    painter.fillPath(path, gradient)

    # 绘制 "WP" 文字
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setFamily("Segoe UI")
    font.setPointSize(size // 3)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        QRectF(0, 0, size, size),
        Qt.AlignmentFlag.AlignCenter,
        "WP",
    )
    painter.end()

    icon = QIcon(pixmap)
    logger.info("应用图标已生成: %dx%d", size, size)
    return icon


def create_tray_icon(size: int = 32) -> QIcon:
    """创建一个简化版托盘图标（32x32）。

    Returns:
        QIcon 对象。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 圆角矩形
    radius = size * 0.15
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # 渐变
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#f472b6"))
    gradient.setColorAt(1.0, QColor("#818cf8"))
    painter.fillPath(path, gradient)

    # "W" 字母（简化版，更小更清晰）
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setFamily("Segoe UI")
    font.setPointSize(size // 2)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        QRectF(0, 0, size, size),
        Qt.AlignmentFlag.AlignCenter,
        "W",
    )
    painter.end()

    icon = QIcon(pixmap)
    logger.info("托盘图标已生成: %dx%d", size, size)
    return icon
