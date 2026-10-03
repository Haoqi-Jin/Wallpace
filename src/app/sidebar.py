"""src/app/sidebar.py — Narrow icon sidebar navigation component.

Mockup target: 60px wide icon sidebar, hover tooltips, brand logo at top,
action buttons (switch/skip/favorite) at bottom.
"""

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QPushButton,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SidebarButton — single icon nav button
# ---------------------------------------------------------------------------

class SidebarButton(QPushButton):
    """Single navigation button in the icon sidebar."""

    def __init__(self, icon_char: str, label: str, page_id: str,
                 parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._icon_char = icon_char
        self._label = label
        self._page_id = page_id
        self.setFixedSize(60, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("nav_button")
        self.setCheckable(True)
        self.setStyleSheet(self._base_style())

    @property
    def page_id(self) -> str:
        return self._page_id

    def _base_style(self) -> str:
        active_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0," \
                     " stop:0 #fce7f3, stop:1 #e0e7ff)"
        hover_bg = QColor("#f9fafb").name()
        return (
            "QPushButton#nav_button {"
            "border: none; border-radius: 10px;"
            "padding: 0; background: transparent;"
            "}"
            "QPushButton#nav_button:hover {"
            f"background-color: {hover_bg};"
            "}"
            "QPushButton#nav_button:checked {"
            f"background: {active_bg};"
            "}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        checked = self.isChecked()
        color = QColor("#7c3aed" if checked else "#6b7280")
        font = QFont("Segoe UI", 20)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._icon_char)
        painter.end()

    def enterEvent(self, event):
        pos = self.mapToGlobal(self.rect().topRight())
        pos.setX(pos.x() + 8)
        QToolTip.showText(pos, self._label)
        super().enterEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
# ActionButton — bottom action buttons (switch / skip / favorite)
# ---------------------------------------------------------------------------

class ActionButton(QPushButton):
    """Small circular action button at the bottom of the sidebar."""

    clicked_action = Signal(str)  # emits action name when clicked

    ICON_SIZE = 18

    def __init__(self, icon_default: str, icon_active: str, tooltip: str, action_name: str,
                 parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._icon_default = icon_default
        self._icon_active = icon_active
        self._is_active = False
        self._action_name = action_name
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setStyleSheet(self._style())
        self.clicked.connect(lambda _: self.clicked_action.emit(action_name))

    def _style(self) -> str:
        return (
            "QPushButton {"
            "border: 2px solid #e5e7eb; border-radius: 22px;"
            "background: white; padding: 0;"
            "font-size: 18px; color: #6b7280;"
            "}"
            "QPushButton:hover {"
            "border-color: #ec4899; color: #ec4899;"
            "background: #fdf2f8;"
            "}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", self.ICON_SIZE))
        painter.setPen(QColor(self.palette().color(self.foregroundRole())))
        icon = self._icon_active if self._is_active else self._icon_default
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, icon)
        painter.end()

    def set_active(self, active: bool) -> None:
        """Toggle the button's active state and repaint."""
        self._is_active = active
        self.update()


# ---------------------------------------------------------------------------
# BrandLogoWidget — "WP" gradient logo at the top of sidebar
# ---------------------------------------------------------------------------

class BrandLogoWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setFixedWidth(60)
        self.setToolTip("Wallpace")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Gradient background
        from PySide6.QtGui import QLinearGradient
        grad = QLinearGradient(0, 0, 0, 48)
        grad.setColorAt(0, QColor("#f472b6"))
        grad.setColorAt(1, QColor("#818cf8"))
        painter.fillRect(self.rect(), grad)

        # White text
        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "WP")


# ---------------------------------------------------------------------------
# SidebarNavigation — compact 60px icon sidebar
# ---------------------------------------------------------------------------

class SidebarNavigation(QWidget):
    """Compact icon sidebar navigation (60px wide).

    Contains:
      - Brand logo at top ("WP" gradient tile)
      - Page navigation icons (home, gallery, clock, settings)
      - Bottom action buttons (switch, skip, favorite)
    """

    WIDTH = 60

    # Icon chars mapped to page IDs
    NAV_ITEMS = [
        ("\u2328", "home", "壁纸预览"),     # Desktop icon
        ("\u25b6", "gallery", "图片库"),     # Play/gallery icon
        ("\u23f1", "clock", "时间"),         # Clock icon
        ("\u2699", "settings", "设置"),      # Gear icon
    ]

    ACTION_ITEMS = [
        ("↺", "↺", "换一张", "switch"),
        ("⏭", "⏭", "跳过", "skip"),
        ("☆", "★", "收藏", "favorite"),
    ]

    def __init__(self, stacked: QStackedWidget,
                 on_settings_open: Callable[[], None],
                 on_action_switch: Callable[[], None] | None = None,
                 on_action_skip: Callable[[], None] | None = None,
                 on_action_favorite: Callable[[], None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stacked = stacked
        self._on_settings_open = on_settings_open
        self._on_action_switch = on_action_switch
        self._on_action_skip = on_action_skip
        self._on_action_favorite = on_action_favorite
        self._buttons: dict[str, SidebarButton] = {}
        self._fav_state: bool = False  # track favorite toggle

        self.setFixedWidth(self.WIDTH)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(4)

        # === Brand logo ===
        logo = BrandLogoWidget(self)
        layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addSpacing(8)

        # === Navigation buttons ===
        for icon_char, page_id, label in self.NAV_ITEMS:
            btn = SidebarButton(icon_char, label, page_id, self)
            btn.toggled.connect(
                lambda checked, pid=page_id:
                    self._on_page_toggled(pid, checked))
            self._buttons[page_id] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

        # === Separator line ===
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setFixedWidth(36)
        sep.setObjectName("sidebar_sep")
        sep.setStyleSheet("background-color: #e5e7eb; border-radius: 1px;")
        layout.addWidget(sep, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(4)

        # === Action buttons (switch / skip / favorite) ===
        self._action_buttons = []  # track for toggle access
        for icon_def, icon_act, tooltip, action_key in self.ACTION_ITEMS:
            act_btn = ActionButton(icon_def, icon_act, tooltip, action_key, self)
            self._action_buttons.append(act_btn)
            act_btn.clicked.connect(self._handle_action)
            layout.addWidget(act_btn)
            act_btn.setToolTip(tooltip)

        layout.addStretch(2)

        # Default: select home
        self._buttons["home"].setChecked(True)
        self._stacked.setCurrentIndex(0)

    def _handle_action(self, action_name: str) -> None:
        """Route action button clicks to callbacks."""
        if action_name == "switch" and self._on_action_switch:
            self._on_action_switch()
        elif action_name == "skip" and self._on_action_skip:
            self._on_action_skip()
        elif action_name == "favorite" and self._on_action_favorite:
            self._on_action_favorite()

    def set_favorite_state(self, state: bool) -> None:
        """Update the favorite button visual."""
        self._fav_state = state
        # We'd need a reference to the star button; for now just log
        logger.debug("Favorite state changed: %s", state)

    def _on_page_toggled(self, page_id: str, checked: bool) -> None:
        if not checked:
            return
        idx_map = {name: pos for pos, (_, name, _) in enumerate(self.NAV_ITEMS)}
        if page_id in idx_map:
            self._stacked.setCurrentIndex(idx_map[page_id])
        if page_id == "settings":
            self._on_settings_open()

    def update_status(self, text: str) -> None:
        pass  # No status label in narrow sidebar

    def set_active_button(self, page_id: str) -> None:
        for pid, btn in self._buttons.items():
            btn.setChecked(pid == page_id)
