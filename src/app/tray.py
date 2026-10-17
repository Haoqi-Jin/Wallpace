"""src/app/tray.py — 系统托盘图标和右键菜单。"""

import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from src.app.icon import create_tray_icon

if TYPE_CHECKING:
    from src.app.window import MainWindow

logger = logging.getLogger(__name__)


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标，支持最小化到托盘和快捷操作。

    提供菜单项：换一张、暂停/继续、打开设置、退出。
    """

    def __init__(self, main_window: "MainWindow",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._is_paused = False
        self._create_menu()
        self.setContextMenu(self._menu)

        self.activated.connect(self.on_activated)
        self.setIcon(create_tray_icon(32))
        self.setToolTip("Wallpace")

    def _create_menu(self) -> None:
        self._menu = QMenu()

        # 换一张
        self._act_switch = QAction("\u6362\u4e00\u5f20", self._menu)
        self._act_switch.triggered.connect(self._on_switch)
        self._menu.addAction(self._act_switch)

        self._menu.addSeparator()

        # 暂停/继续
        self._act_pause = QAction("\u6682\u505c\u5207\u6362", self._menu)
        self._act_pause.triggered.connect(self._on_pause_toggle)
        self._menu.addAction(self._act_pause)

        # 打开主窗口
        self._act_show = QAction("\u6253\u5f00\u7a97\u53e3", self._menu)
        self._act_show.triggered.connect(self._on_show_window)
        self._menu.addAction(self._act_show)

        # G07：诊断入口。窗口最小化到托盘后，用户最常在这里找"出了事怎么办"，
        # 所以菜单同样暴露日志目录（委托 MainWindow → SettingsController）。
        self._act_logs = QAction("打开日志目录", self._menu)
        self._act_logs.triggered.connect(self._on_open_logs)
        self._menu.addAction(self._act_logs)

        self._menu.addSeparator()

        # 退出
        self._act_exit = QAction("\u9000\u51fa", self._menu)
        self._act_exit.triggered.connect(self._on_exit)
        self._menu.addAction(self._act_exit)

    def set_active(self, is_active: bool) -> None:
        self._act_switch.setEnabled(is_active)
        self._act_exit.setEnabled(is_active)

    def update_pause_status(self, paused: bool) -> None:
        self._is_paused = paused
        if paused:
            self._act_pause.setText("\u7ee7\u7eed\u5207\u6362")
            self.setToolTip("Wallpace (\u5df2\u6682\u505c)")
        else:
            self._act_pause.setText("\u6682\u505c\u5207\u6362")
            self.setToolTip("Wallpace (\u8fd0\u884c\u4e2d)")

    # ===== 槽函数 =====

    def _on_switch(self) -> None:
        self._main_window.request_switch()

    def _on_pause_toggle(self) -> None:
        if self._is_paused:
            self._main_window.resume_scheduler()
        else:
            self._main_window.pause_scheduler()

    def _on_show_window(self) -> None:
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _on_open_logs(self) -> None:
        self._main_window._on_open_logs()

    def _on_exit(self) -> None:
        logger.info("\u4ece\u6258\u76d8\u9000\u51fa")
        self._main_window.quit_app()

    def on_activated(self, reason: int) -> None:
        """左键点击托盘图标快速切一张。"""
        if reason == 1 and not self._is_paused:
            self._main_window.request_switch()
