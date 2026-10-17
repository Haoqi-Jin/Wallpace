"""src/app/pages/settings_page.py — 设置页面（视图层）。

只负责设置页 UI 的构建与纯 UI 状态更新；业务逻辑（调度器、注册表、
信息卡片等）由 MainWindow 通过回调驱动。这样 window.py 仍保留对
AutostartManager 等模块的引用关系，既完成了「拆分 window.py」的架构重构，
又兼容既有测试（例如对 src.app.window.AutostartManager 的 monkeypatch）。
"""

import logging
from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import COLOR_GRAY_100

logger = logging.getLogger(__name__)

MODE_DISPLAY_MAP = {
    "daily_random": "每日随机",
    "interval_minutes": "间隔时间",
    "manual": "手动",
}
MODE_DESCRIPTIONS = {
    "daily_random": "每天指定时间随机切换一张壁纸",
    "interval_minutes": "每隔设定的时间自动切换",
    "manual": "只响应手动切换操作，无自动切换",
}


class SettingsPage(QWidget):
    """设置页面：图片文件夹、切换模式、通用开关。

    构造时传入 settings 与一组回调（由 MainWindow 提供业务逻辑）。页面自身
    不触碰调度器 / 注册表 / 主窗口状态，只维护内部控件与列表展示。
    """

    def __init__(
        self,
        settings,
        callbacks: dict[str, Callable],
        parent: QWidget = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cb = callbacks
        self._build_ui()

    @property
    def autostart_check(self) -> QCheckBox:
        """暴露开机自启复选框，供 MainWindow / 测试访问。"""
        return self._autostart_check

    @property
    def minimize_check(self) -> QCheckBox:
        """暴露最小化到托盘复选框，供 MainWindow / 测试访问。"""
        return self._minimize_check

    @property
    def per_monitor_check(self) -> QCheckBox:
        """暴露「逐屏独立壁纸」复选框，供 MainWindow / 测试访问。"""
        return self._per_monitor_check

    # ==================== 构建 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)

        title = QLabel("设置")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addSpacing(8)

        # --- 分组: 图片库 ---
        group_images = QFrame()
        group_images.setObjectName("settings_group")
        self._group_images = group_images  # 扫描期间整体禁用
        group_layout = QVBoxLayout(group_images)
        group_layout.setContentsMargins(12, 12, 12, 12)

        dir_label = QLabel("图片文件夹")
        dir_label.setObjectName("sub-title")
        group_layout.addWidget(dir_label)

        # 目录列表（每个目录显示路径 + 删除按钮）
        self._dirs_layout = QVBoxLayout()
        self._dirs_layout.setSpacing(4)
        self.refresh_dir_list()
        group_layout.addLayout(self._dirs_layout)

        # 添加/删除按钮行
        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加文件夹")
        btn_add.setStyleSheet(
            "QPushButton { padding: 6px 16px; border-radius: 6px; }"
        )
        btn_add.clicked.connect(self._cb["on_add_directory"])
        btn_row.addWidget(btn_add)
        self._btn_add_dir = btn_add  # 扫描期间禁用

        btn_remove = QPushButton("移除文件夹")
        btn_remove.setStyleSheet(
            "QPushButton { padding: 6px 16px; border-radius: 6px; "
            "color: #c62828; border: 1px solid #ffcdd2; }"
        )
        btn_remove.clicked.connect(self._cb["on_remove_directory"])
        btn_row.addWidget(btn_remove)
        self._btn_remove_dir = btn_remove  # 扫描期间禁用
        btn_row.addStretch()
        group_layout.addLayout(btn_row)

        # 显示当前配置的图片目录数量
        dirs = self._settings.get("image_directories", [])
        self._dir_count_label = QLabel(f"共 {len(dirs)} 个目录")
        self._dir_count_label.setStyleSheet(
            "font-size: 11px; color: #9e9e9e; margin-top: 4px;"
        )
        group_layout.addWidget(self._dir_count_label)

        layout.addWidget(group_images)
        layout.addSpacing(16)

        # --- 分组: 切换策略 ---
        group_switch = QFrame()
        group_switch.setObjectName("settings_group")
        switch_layout = QVBoxLayout(group_switch)
        switch_layout.setContentsMargins(12, 12, 12, 12)

        mode_label = QLabel("切换模式")
        mode_label.setObjectName("sub-title")
        switch_layout.addWidget(mode_label)

        # 模式选择器
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["每日随机", "间隔时间", "手动"])
        current_mode = self._settings.get("switch_mode", "daily_random")
        display_text = MODE_DISPLAY_MAP.get(current_mode, "每日随机")
        self._mode_combo.setCurrentText(display_text)
        self._mode_combo.currentTextChanged.connect(self._cb["on_mode_changed"])
        switch_layout.addWidget(self._mode_combo)

        # 显示当前模式描述
        self._mode_desc = QLabel("")
        self._mode_desc.setStyleSheet(
            "font-size: 12px; color: #424242; margin-top: 4px;"
        )
        self.update_mode_description(current_mode)
        switch_layout.addWidget(self._mode_desc)

        # 间隔时间输入框（仅间隔模式可见）
        self._interval_group = QFrame()
        interval_layout = QVBoxLayout(self._interval_group)
        interval_layout.setContentsMargins(0, 4, 0, 0)
        interval_label = QLabel("间隔时间（分钟）:")
        interval_label.setStyleSheet("font-size: 12px;")
        self._interval_input = QLineEdit()
        self._interval_input.setPlaceholderText("例如：30")
        self._interval_input.setMaximumWidth(100)
        interval_layout.addWidget(interval_label)

        interval_input_row = QHBoxLayout()
        interval_input_row.addWidget(self._interval_input)
        btn_apply_interval = QPushButton("应用")
        btn_apply_interval.setFixedHeight(26)
        btn_apply_interval.setStyleSheet(
            "QPushButton { padding: 2px 12px; border-radius: 6px; "
            "background-color: #5c6bc0; color: white; font-weight: 600; font-size: 12px; }"
            "QPushButton:hover { background-color: #7986cb; }"
        )
        btn_apply_interval.clicked.connect(self._cb["on_apply_interval"])
        interval_input_row.addWidget(btn_apply_interval)
        interval_input_row.addStretch()
        interval_layout.addLayout(interval_input_row)

        self._interval_group.setVisible(False)
        switch_layout.addWidget(self._interval_group)

        layout.addWidget(group_switch)
        layout.addSpacing(16)

        # --- 分组: 通用 ---
        group_general = QFrame()
        group_general.setObjectName("settings_group")
        general_layout = QVBoxLayout(group_general)
        general_layout.setContentsMargins(12, 12, 12, 12)

        general_title = QLabel("通用")
        general_title.setObjectName("sub-title")
        general_layout.addWidget(general_title)

        # 开机自启
        self._autostart_check = QCheckBox("开机自启")
        self._autostart_check.setChecked(
            # 兜底默认值必须是 False：与 DEFAULT_CONFIG 一致（G03）。
            # 老版本配置缺键时若用 True 兜底，复选框会误显示"已勾选"。
            bool(self._settings.get("auto_start", False))
        )
        self._autostart_check.toggled.connect(self._cb["on_autostart_toggle"])
        general_layout.addWidget(self._autostart_check)

        # G02：卸载清理入口。勾选会移除开机自启项与 ~/.wallpace 配置/日志，
        # 属不可逆动作，故用按钮显式触发（并二次确认），而非静默/勾选式。
        self._uninstall_cleanup_btn = QPushButton("卸载清理")
        self._uninstall_cleanup_btn.setToolTip(
            "移除开机自启项，并删除 ~/.wallpace 下的配置/收藏/日志。"
            "此操作不可撤销。"
        )
        self._uninstall_cleanup_btn.clicked.connect(
            self._cb["on_uninstall_cleanup"]
        )
        general_layout.addWidget(self._uninstall_cleanup_btn)

        # G07：诊断入口。用户出问题时能自己找到日志（~/.wallpace/logs/），
        # 不再需要读文档/翻注册表猜位置。
        self._open_logs_btn = QPushButton("打开日志目录（诊断）")
        self._open_logs_btn.setToolTip(
            "在文件管理器中打开 ~/.wallpace/logs，"
            "报错时可把 wallpace.log 发给维护者排查。"
        )
        self._open_logs_btn.clicked.connect(self._cb["on_open_logs"])
        general_layout.addWidget(self._open_logs_btn)

        # 关闭窗口时最小化到托盘
        self._minimize_check = QCheckBox("关闭窗口时最小化到托盘")
        self._minimize_check.setChecked(
            bool(self._settings.get("minimize_to_tray", True))
        )
        self._minimize_check.toggled.connect(self._cb["on_minimize_toggle"])
        general_layout.addWidget(self._minimize_check)

        # 逐屏独立壁纸（#7）：仅当平台支持且检测到 >=2 块显示器时才可用，
        # 否则由 MainWindow 调用 set_per_monitor_available(False, 原因) 置灰。
        self._per_monitor_check = QCheckBox("逐屏独立壁纸")
        self._per_monitor_check.setChecked(
            self._settings.get("wallpaper_mode", "unified") == "per_monitor"
        )
        self._per_monitor_check.toggled.connect(
            self._cb["on_wallpaper_mode_toggle"]
        )
        general_layout.addWidget(self._per_monitor_check)

        layout.addWidget(group_general)
        layout.addStretch()

    # ==================== 纯 UI 辅助 ====================

    def refresh(self) -> None:
        """刷新目录计数标签与目录列表（对应原 _update_settings_page_display）。"""
        dirs = self._settings.get("image_directories", [])
        self._dir_count_label.setText(f"共 {len(dirs)} 个目录")
        self.refresh_dir_list()

    def refresh_dir_list(self) -> None:
        """重建设置页面的目录列表显示。

        注意：必须用 takeAt 先把 item 从布局中移除再 deleteLater，否则
        count() 不会减少（删除事件要等事件循环处理），会造成死循环卡死。
        """
        while self._dirs_layout.count():
            item = self._dirs_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        dirs = self._settings.get("image_directories", [])
        if not dirs:
            empty_label = QLabel("未配置图片文件夹，请点击「添加文件夹」")
            empty_label.setStyleSheet(
                f"background-color: {COLOR_GRAY_100}; "
                "border-radius: 6px; padding: 8px; font-size: 12px; color: #9e9e9e;"
            )
            self._dirs_layout.addWidget(empty_label)
            return

        for i, d in enumerate(dirs):
            row = QHBoxLayout()
            path_label = QLabel(f"  {d}")
            path_label.setWordWrap(True)
            path_label.setStyleSheet(
                f"background-color: {COLOR_GRAY_100}; "
                "border-radius: 6px; padding: 6px 8px; font-size: 12px;"
            )
            path_label.setProperty("dir_index", i)
            row.addWidget(path_label, stretch=1)

            remove_btn = QPushButton("✕")
            remove_btn.setFixedSize(24, 24)
            remove_btn.setStyleSheet(
                "QPushButton { border: none; border-radius: 12px; "
                "background-color: #ffcdd2; color: #c62828; font-weight: bold; }"
                "QPushButton:hover { background-color: #ef9a9a; }"
            )
            remove_btn.clicked.connect(
                lambda _checked, p=d: self._cb["on_remove_single_dir"](p)
            )
            row.addWidget(remove_btn)

            frame = QFrame()
            frame.setLayout(row)
            self._dirs_layout.addWidget(frame)

    def update_mode_description(self, mode: str) -> None:
        """更新模式描述标签。"""
        self._mode_desc.setText(MODE_DESCRIPTIONS.get(mode, ""))

    def set_interval_visible(self, visible: bool) -> None:
        """显示/隐藏间隔时间输入框（仅间隔模式）。"""
        self._interval_group.setVisible(visible)

    def set_interval_value(self, value) -> None:
        """将已有的间隔值显示到输入框。"""
        self._interval_input.setText(str(value))

    def get_interval_text(self) -> str:
        """读取间隔时间输入框内容（已去除首尾空白）。"""
        return self._interval_input.text().strip()

    def set_controls_enabled(self, enabled: bool) -> None:
        """扫描进行中整体禁用目录与模式控件。"""
        for w in (
            self._group_images,
            self._btn_add_dir,
            self._btn_remove_dir,
            self._mode_combo,
        ):
            w.setEnabled(enabled)

    def set_per_monitor_available(self, available: bool, reason: str = "") -> None:
        """设置「逐屏独立壁纸」开关的可用性。

        不可用时置灰并给出 tooltip 说明原因（例如「当前系统不支持逐屏壁纸」），
        避免做成"点了没反应"的死开关。

        Args:
            available: 是否可用。
            reason: 不可用时的原因文案（可用时忽略）。
        """
        self._per_monitor_check.setEnabled(available)
        self._per_monitor_check.setToolTip(
            "" if available else (reason or "当前系统不支持逐屏壁纸")
        )
