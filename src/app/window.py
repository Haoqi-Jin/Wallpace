"""src/app/window.py — 主窗口 (B 方案: 图标侧边栏 + 粉蓝渐变)。

包含：顶栏、侧边导航、QStackedWidget 内容区、底部状态栏。
遵循 MVC 分离：UI 只负责展示和用户交互，业务逻辑委托给 core 层。
"""

import logging
import random
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    Qt,
    Slot,
    QTimer,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    # QFileDialog 的业务调用已迁至 SettingsController，但此处**保留导入**：
    # 既有测试以 "src.app.window.QFileDialog.getExistingDirectory" 为 monkeypatch
    # 锚点（作用于共享的类对象，迁移后仍生效），移除导入会使该锚点解析失败。
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from src.core.image_library import ImageLibrary
    from src.core.scheduler import Scheduler
    from src.core.settings import Settings
    from src.core.wallpaper_manager import WallpaperManager

from src.app.sidebar import SidebarNavigation
from src.core.directory_watcher import DirectoryWatcher
from src.app.theme import (
    COLOR_BLUE_DARK,
    COLOR_GRAY_100,
    COLOR_GRAY_50,
        COLOR_GRAY_500,
    COLOR_GRAY_800,
    COLOR_PINK_LIGHT,
    COLOR_BLUE_LIGHT,
    ThemeManager,
)
from src.app.tray import TrayIcon
from src.app.widgets.preview_card import PreviewCardWidget
from src.app.icon import create_app_icon
from src.app import image_loader
from src.app.scan_controller import ScanController
from src.app.settings_controller import SettingsController
from src.app.widgets.gallery_strip import GalleryStrip
from src.core.autostart_registry import AutostartManager
from src.app.pages.gallery_page import GalleryPage
from src.app.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """主窗口，承载整个应用的 UI。

    结构：
        窗口
        ├── 顶栏（渐变背景 + 应用名称）
        └── 中心区域（水平布局）
            ├── 侧边栏导航（导航按钮 -> 切换 QStackedWidget）
            └── QStackedWidget
                ├── 页面 0: 壁纸预览 (PreviewCardWidget)
                ├── 页面 1: 图片库缩略图网格
                ├── 页面 2: 时钟信息页
                └── 页面 3: 设置面板
        └── 底部状态栏
    """

    SWITCH_REQUESTED = 1
    FAVORITE_REQUESTED = 2

    def __init__(
        self,
        settings: "Settings",
        library: "ImageLibrary",
        wallpaper_manager: "WallpaperManager",
        scheduler: Optional["Scheduler"] = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._settings = settings
        self._force_quit = False  # 是否强制退出（绕过最小化到托盘）
        self._library = library
        # 从持久化配置还原 skip/favorites（避免重启后用户收藏/跳过丢失）
        self._library.load_persisted_state(
            self._settings.get("skip_list", []),
            self._settings.get("favorites", []),
        )
        self._wallpaper_manager = wallpaper_manager
        self._scheduler = scheduler
        # #7：给调度器注入"按模式分流"的壁纸设置器，避免逐屏模式下先统一设一张
        # 再被覆盖（双写 + 闪烁）。注入后调度器不再直接调 WallpaperManager。
        if self._scheduler is not None and hasattr(
            self._scheduler, "set_wallpaper_applier"
        ):
            self._scheduler.set_wallpaper_applier(self._apply_wallpaper_for_mode)

        self._current_image_index = 0
        self._clock_timer: Optional["QTimer"] = None
        # 时钟文本去重缓存（#8）：文本未变化时不再 setText，避免 1 Hz 无效重绘
        self._last_clock_time_text: Optional[str] = None
        self._last_clock_date_text: Optional[str] = None
        self._last_next_text: Optional[str] = None

        # --- 异步扫描基础设施（#1：状态机已提取到 ScanController）---
        # 专用单线程池 + running/pending 合并补扫，均封装在 ScanController 内。
        self._scan = ScanController(self._library, self)
        self._scan.started.connect(self._on_scan_started)
        self._scan.finished.connect(self._on_scan_finished)

        # --- 文件夹变更监听（watchdog 递归监听 + 防抖后触发重扫） ---
        self._watcher = DirectoryWatcher(
            extensions=self._settings.get("extensions", []),
            parent=self,
        )
        self._watcher.changes_detected.connect(self._on_dirs_changed)
        self._watcher.set_directories(self._settings.get("image_directories", []))

        self._setup_window()
        self._setup_ui()
        # 设置页业务逻辑控制器（#1：7 个回调方法已提取，facade 转发；需在
        # _setup_ui 之后创建以便拿到 settings_page）。scheduler / 扫描状态用
        # provider 延迟求值，兼容既有测试对 main_window._scheduler 的重赋值；
        # AutostartManager 用 lambda 工厂延迟解析，使 src.app.window 上的
        # monkeypatch 生效。
        self._settings_ctrl = SettingsController(
            settings=self._settings,
            library=self._library,
            scheduler_provider=lambda: self._scheduler,
            settings_page=self._settings_page,
            wallpaper_manager=self._wallpaper_manager,
            scan_state=lambda: self._scan_running,
            refresh_gallery=lambda: self._refresh_gallery(),
            refresh_status=self._refresh_status,
            set_watcher_directories=lambda dirs: self._watcher.set_directories(dirs),
            on_switch=self.on_wallpaper_switched,
            autostart_manager_factory=lambda: AutostartManager(),
        )
        self._start_clock_timer()

    def _setup_window(self) -> None:
        """窗口基本属性。"""
        self.setWindowTitle("Wallpace")
        self.setMinimumSize(800, 540)
        self.resize(960, 620)
        self.setWindowIcon(create_app_icon(256))
        logger.info("主窗口初始化完成")

    def show_platform_warning(self, message: str) -> None:
        """在窗口顶部显示平台能力警告条（非阻塞、持续可见）。

        用于把「当前平台不支持设置系统壁纸」这类致命但不足以阻止启动的问题
        明确告知用户 —— 此前只在日志里记一条 error，打包成 --windowed 后用户
        没有控制台可看，界面一切正常却点「换一张」静默失败。

        Args:
            message: 警告文案。
        """
        banner = getattr(self, "_platform_banner", None)
        if banner is None:
            logger.warning("%s", message)
            return
        banner.setText(f"⚠ {message}")
        banner.setVisible(True)
        logger.warning("已显示平台警告: %s", message)

    def _setup_ui(self) -> None:
        """构建所有 UI 组件。"""
        theme = ThemeManager()
        self.setStyleSheet(theme.light_qss())

        # 中心容器
        central = QWidget()
        self.setCentralWidget(central)
        # === 顶部渐变栏 ===
        top_bar = QFrame()
        top_bar.setObjectName("top_bar")
        top_bar.setFixedHeight(60)
        top_bar.setStyleSheet(theme.top_bar_gradient_css())
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(20, 10, 20, 10)

        self._top_title = QLabel("Wallpace")
        self._top_title.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold;"
        )
        top_bar_layout.addWidget(self._top_title)

        self._top_status = QLabel("● 未启动")
        self._top_status.setStyleSheet(
            "color: rgba(255,255,255,0.7); font-size: 12px;"
        )
        top_bar_layout.addWidget(self._top_status)
        top_bar_layout.setStretchFactor(self._top_status, 0)
        # 为顶栏状态添加点击事件（用于恢复暂停的调度器）
        self._top_status.mousePressEvent = self._on_top_status_click

        # === 侧边栏 + 内容区 ===
        body_layout = QHBoxLayout()
        body_layout.setSpacing(0)
        body_layout.setContentsMargins(0, 0, 0, 0)

        # 分隔线
        divider = QFrame()
        divider.setObjectName("sidebar_divider")
        divider.setFixedWidth(1)
        divider.setStyleSheet("background-color: #eeeeee;")

        # 右侧堆叠页面
        self._stacked = QStackedWidget()
        self._stacked.setContentsMargins(0, 0, 0, 0)

        # ---- 页面 0: 壁纸预览 ----
        self._preview_page = self._build_preview_page()
        self._stacked.addWidget(self._preview_page)

        # ---- 页面 1: 图片库（占位） ----
        self._gallery_page = self._build_gallery_page()
        self._stacked.addWidget(self._gallery_page)

        # ---- 页面 2: 时钟（占位） ----
        clock_placeholder = self._build_clock_placeholder()
        self._stacked.addWidget(clock_placeholder)

        # ---- 页面 3: 设置面板 ----
        self._settings_page = self._build_settings_page()
        self._stacked.addWidget(self._settings_page)

        # 按平台/COM/显示器数初始化「逐屏独立壁纸」开关的可用性
        self._update_multi_monitor_availability()

        body_layout.addWidget(divider)
        body_layout.addWidget(self._stacked, stretch=1)

        # 侧边栏
        self._sidebar = SidebarNavigation(
            stacked=self._stacked,
            on_settings_open=self.open_settings,
            on_action_switch=self.request_switch,  # 虽然按钮被移除，但保留以防未来使用
            on_action_skip=self._handle_skip,
            on_action_favorite=self._handle_favorite,  # 连接到收藏处理
        )
        body_layout.insertWidget(0, self._sidebar)

        # 合并到中心布局
        vertical_layout = QVBoxLayout(central)
        vertical_layout.setContentsMargins(0, 0, 0, 0)
        vertical_layout.setSpacing(0)
        vertical_layout.addWidget(top_bar)

        # === 平台能力警告条（默认隐藏；非 Windows 时由 main.py 调用 show_platform_warning）===
        self._platform_banner = QLabel()
        self._platform_banner.setObjectName("platform_banner")
        self._platform_banner.setWordWrap(True)
        self._platform_banner.setStyleSheet(
            "background-color: #fff3cd; color: #8a6d3b;"
            "border-bottom: 1px solid #ffeeba;"
            "padding: 6px 16px; font-size: 12px;"
        )
        self._platform_banner.setVisible(False)
        vertical_layout.addWidget(self._platform_banner)

        vertical_layout.addLayout(body_layout)

        # === Bottom status bar ===
        self._bottom_bar = QFrame()
        self._bottom_bar.setObjectName("bottom_bar")
        self._bottom_bar.setFixedHeight(28)
        bottom_layout = QHBoxLayout(self._bottom_bar)
        bottom_layout.setContentsMargins(16, 4, 16, 4)
        bottom_layout.setSpacing(12)

        self._bottom_source_label = QLabel()
        self._bottom_source_label.setStyleSheet("font-size: 11px; color: #9e9e9e;")
        bottom_layout.addWidget(self._bottom_source_label)

        self._bottom_notifier_label = QLabel()
        self._bottom_notifier_label.setStyleSheet("font-size: 11px; color: #9e9e9e;")
        bottom_layout.addWidget(self._bottom_notifier_label)

        self._bottom_fav_count = QLabel()
        self._bottom_fav_count.setStyleSheet("font-size: 11px; color: #9e9e9e;")
        bottom_layout.addWidget(self._bottom_fav_count)

        bottom_layout.addStretch()

        self._bottom_version = QLabel("v0.1.0")
        self._bottom_version.setStyleSheet("font-size: 11px; color: #bdbdbd;")
        bottom_layout.addWidget(self._bottom_version)

        vertical_layout.addWidget(self._bottom_bar)

        # === 托盘 ===
        self._tray = TrayIcon(self)
        self._tray.show()

        # === 初始化数据 ===
        self._refresh_gallery()
        logger.info("UI 初始化完成")

    # ===== 页面构建 =====

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)

        # 标题行
        title_row = QHBoxLayout()
        title_row.setContentsMargins(24, 0, 0, 0)

        current_label = QLabel("当前壁纸")
        current_label.setObjectName("subtitle")
        title_row.addWidget(current_label)

        title_row.addStretch()

        date_label = QLabel()
        date_label.setObjectName("sub-title")
        date_label.setText(self._get_date_string())
        title_row.addWidget(date_label)

        layout.addLayout(title_row)

        # 空状态引导提示（默认隐藏）
        self._empty_guide = QLabel(
            "📂 还没有添加图片文件夹\n"
            "点击左侧「设置」→「添加文件夹」来选择壁纸文件夹"
        )
        self._empty_guide.setObjectName("empty_guide")
        self._empty_guide.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_guide.setWordWrap(True)
        self._empty_guide.setStyleSheet(
            "QLabel#empty_guide { "
            "background-color: #fafafa; "
            "border: 2px dashed #e0e0e0; border-radius: 12px; "
            "padding: 40px 20px; "
            "font-size: 14px; color: #9e9e9e; "
            "margin: 40px 24px; "
            "}"
        )
        self._empty_guide.setVisible(False)
        layout.addWidget(self._empty_guide)

        # 预览卡片
        scroll = QFrame()
        scroll.setStyleSheet("QFrame { background-color: transparent; }")
        scroll_scroll_layout = QVBoxLayout(scroll)
        scroll_scroll_layout.setContentsMargins(24, 8, 24, 24)
        scroll_scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._preview_card = PreviewCardWidget()
        self._preview_card.switch_clicked.connect(self._handle_switch)
        self._preview_card.skip_clicked.connect(self._handle_skip)
        self._preview_card.favorite_clicked.connect(self._handle_favorite)
        scroll_scroll_layout.addWidget(self._preview_card)
        layout.addWidget(scroll, stretch=1)

        # === Info Grid (3 stat cards) ===
        info_grid = QFrame()
        info_grid.setObjectName("info_grid")
        info_layout = QHBoxLayout(info_grid)
        info_layout.setContentsMargins(24, 8, 24, 8)
        info_layout.setSpacing(12)

        mode_name = self._settings.get("switch_mode", "daily_random")
        mode_display = {"daily_random": "每日随机", "interval_minutes": f"每{self._settings.get('interval_minutes', 60)}分钟", "manual": "手动"}
        self._method_card = self._make_info_card("切换方式", mode_display.get(mode_name, "每日随机"))
        info_layout.addWidget(self._method_card)

        info_layout.addSpacing(12)

        total = self._library.total_count
        next_time = f"明天 {self._settings.get('daily_time', '08:00')}" if total > 0 else "添加图片后"
        self._next_time_card = self._make_info_card(
            "下次切换", next_time, highlight=True
        )
        info_layout.addWidget(self._next_time_card)

        info_layout.addSpacing(12)

        self._count_card = self._make_info_card(
            "图片总数", str(total)
        )
        info_layout.addWidget(self._count_card)

        layout.addWidget(info_grid)

        # === Gallery Horizontal Scroll Section ===
        gallery_section = QFrame()
        gallery_section.setObjectName("gallery-section")
        gallery_layout = QVBoxLayout(gallery_section)
        gallery_layout.setContentsMargins(24, 12, 24, 12)
        gallery_layout.setSpacing(8)

        gallery_title = QLabel("图片库")
        gallery_title.setObjectName("subtitle")
        gallery_layout.addWidget(gallery_title)

        # 横向缩略图条（#1：滚动区 + 懒加载状态机已提取到 GalleryStrip）
        self._gallery_strip = GalleryStrip(on_select=self._on_gallery_click)
        gallery_layout.addWidget(self._gallery_strip)

        layout.addWidget(gallery_section)

        return page

    @property
    def _gallery_layout(self):
        """图片库缩略图内部布局（facade → ``GalleryStrip`` 内部 QHBoxLayout）。

        既有测试用 ``_gallery_layout.count()`` 断言缩略图数量，故保持同名 property，
        直返 strip 内部布局（不复制），语义与搬迁前一致。
        """
        return self._gallery_strip.layout()

    def _make_info_card(self, label: str, value: str, highlight: bool = False) -> QFrame:
        card = QFrame()
        card.setObjectName("info-card")
        if highlight:
            card.setProperty("highlight", "true")
            card.setStyleSheet(
                "QFrame#info-card { background-color: transparent; border-radius: 10px; padding: 10px; border: none; } "
                "QFrame#info-card[highlight='true'] { "
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #fce4ec, stop:1 #bbdefb); }"
            )
        else:
            card.setStyleSheet(
                "QFrame#info-card { background-color: #fafafa; border-radius: 10px; padding: 10px; border: 1px solid #eeeeee; }"
            )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)

        lbl = QLabel(label)
        lbl.setObjectName("sub-title")
        lbl.setStyleSheet("font-size: 11px; color: #9e9e9e;")
        card_layout.addWidget(lbl)

        val = QLabel(value)
        val.setObjectName("stat-value")
        val.setStyleSheet("font-size: 16px; font-weight: 600; color: #424242;")
        card_layout.addWidget(val)

        return card

    def _build_gallery_page(self) -> QWidget:
        """构建图片库页面：全部/收藏/已跳过 筛选 + 缩略图网格。"""
        page = GalleryPage(
            library=self._library,
            image_loader=image_loader,
            on_set_wallpaper=self._on_gallery_click,
            on_persist=self._persist_skip_favorites,
        )
        self._gallery_page = page
        return page

    def _build_clock_placeholder(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 16, 24, 16)

        self._clock_time_label = QLabel(self._get_time_string())
        self._clock_time_label.setObjectName("title")
        self._clock_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock_time_label.setStyleSheet(
            f"font-size: 48px; color: {COLOR_BLUE_DARK}; font-weight: bold; "
        )
        layout.addWidget(self._clock_time_label)

        self._clock_date_label = QLabel(self._get_date_string())
        self._clock_date_label.setObjectName("sub-title")
        self._clock_date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock_date_label.setStyleSheet(
            f"font-size: 18px; color: {COLOR_GRAY_500}; margin-bottom: 20px;"
        )
        layout.addWidget(self._clock_date_label)

        # 下次切换信息
        self._clock_next_label = QLabel(self._get_next_switch_text())
        self._clock_next_label.setObjectName("sub-title")
        self._clock_next_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock_next_label.setStyleSheet(
            f"font-size: 14px; color: {COLOR_GRAY_800}; padding: 8px;"
        )
        layout.addWidget(self._clock_next_label)

        layout.addStretch()
        return page

    # ===== 时钟定时器 =====
    def _start_clock_timer(self) -> None:
        """启动一个定时器，每秒更新时钟显示."""
        # 如果有已存在的定时器，先停止并清理
        if self._clock_timer is not None:
            try:
                self._clock_timer.stop()
            except:
                pass
            try:
                self._clock_timer.deleteLater()
            except:
                pass
            self._clock_timer = None
        
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)  # 每秒更新

    def _update_clock(self) -> None:
        """每秒更新时钟页面和顶部日期显示。

        #8 低频刷新：只在文本发生变化时才 ``setText``（比对 ``_last_*`` 缓存），
        避免倒计时/时钟每秒产生无效重绘。仍复用既有 1 Hz ``_clock_timer``。
        """
        now = datetime.now()
        if hasattr(self, "_clock_time_label"):
            time_text = now.strftime("%H:%M:%S")
            if time_text != self._last_clock_time_text:
                self._clock_time_label.setText(time_text)
                self._last_clock_time_text = time_text
        if hasattr(self, "_clock_date_label"):
            date_text = now.strftime("%Y年%m月%d日  %A")
            if date_text != self._last_clock_date_text:
                self._clock_date_label.setText(date_text)
                self._last_clock_date_text = date_text
        if hasattr(self, "_clock_next_label"):
            next_text = self._get_next_switch_text()
            if next_text != self._last_next_text:
                self._clock_next_label.setText(next_text)
                self._last_next_text = next_text

    def _get_next_switch_text(self) -> str:
        """获取「下次切换」描述文本（时钟页与信息卡复用）。

        暂停时冻结为固定文案；运行中显示实时倒计时（秒级）。若调度器在运行但
        无法取到剩余秒数（如单次定时器已触发等边界情况），退回静态文案而非
        显示 ``None``。
        """
        scheduler = self._scheduler
        if scheduler is None:
            return "调度器未启动"
        if scheduler.is_paused:
            return "⏸ 已暂停（倒计时已冻结）"
        if not scheduler.is_active:
            return "● 调度已暂停"

        mode = scheduler.mode
        remaining = scheduler.seconds_until_next()

        if mode == "daily_random":
            if remaining is not None:
                hours, remainder = divmod(remaining, 3600)
                minutes, seconds = divmod(remainder, 60)
                return f"距下次切换还有 {hours}小时{minutes}分{seconds}秒"
            next_time = scheduler.get_next_switch_time()
            if next_time:
                return f"下次切换: {next_time.strftime('%H:%M')}"
            return "下次切换: 明天自动切换"
        if mode == "interval_minutes":
            if remaining is not None:
                minutes, seconds = divmod(remaining, 60)
                return f"距下次切换还有 {minutes}分{seconds}秒"
            mins = scheduler.interval_minutes or 60
            return f"每 {mins} 分钟自动切换一次"
        if mode == "manual":
            return "手动模式，不会自动切换"
        return ""

    def _build_settings_page(self) -> QWidget:
        """构建设置页面：UI 交给 SettingsPage（视图层），业务逻辑回调回 MainWindow。

        这样 window.py 仍保留对 AutostartManager / Scheduler 等的引用，
        既完成了「拆分 window.py」的架构重构，又保持既有测试兼容。
        """
        callbacks = {
            "on_autostart_toggle": self._on_autostart_toggled,
            "on_minimize_toggle": self._on_minimize_toggled,
            "on_mode_changed": self._on_mode_changed,
            "on_add_directory": self._add_directory,
            "on_remove_directory": self._remove_directory,
            "on_apply_interval": self._apply_interval,
            "on_remove_single_dir": self._remove_single_dir,
            "on_wallpaper_mode_toggle": self._on_wallpaper_mode_toggled,
        }
        page = SettingsPage(settings=self._settings, callbacks=callbacks)
        self._settings_page = page
        # 暴露复选框引用，保持既有测试/外部调用兼容
        self._autostart_check = page.autostart_check
        self._minimize_check = page.minimize_check
        return page

    # ===== 设置页面的辅助方法（#1：业务逻辑委托给 SettingsController）=====

    def _on_autostart_toggled(self, checked: bool) -> None:
        """“开机自启”开关（委托 SettingsController）。"""
        self._settings_ctrl.on_autostart_toggled(checked)

    def _on_minimize_toggled(self, checked: bool) -> None:
        """“关闭窗口时最小化到托盘”开关（委托 SettingsController）。"""
        self._settings_ctrl.on_minimize_toggled(checked)

    def _add_directory(self) -> None:
        """打开文件夹选择对话框并添加目录（委托 SettingsController）。"""
        self._settings_ctrl.add_directory()

    def _on_mode_changed(self, text: str) -> None:
        """处理模式切换（委托 SettingsController）。"""
        self._settings_ctrl.on_mode_changed(text)

    def _apply_interval(self) -> None:
        """应用间隔时间设置（委托 SettingsController）。"""
        self._settings_ctrl.apply_interval()

    def _remove_directory(self) -> None:
        """移除最后一个图片目录（委托 SettingsController）。"""
        self._settings_ctrl.remove_directory()

    def _remove_single_dir(self, path: str) -> None:
        """移除指定路径的图片目录（委托 SettingsController）。"""
        self._settings_ctrl.remove_single_dir(path)

    def _refresh_status(self) -> None:
        """刷新信息卡与顶栏状态（供 SettingsController 回调）。"""
        self._update_info_cards()
        self.update_top_status()

    # ===== 业务操作 =====

    def _handle_switch(self) -> None:
        """处理用户点击'换一张'。"""
        if self._scheduler is not None:
            self._scheduler.trigger_now()
            return

        image_path = self._library.get_random()
        if image_path is None:
            logger.warning("无法获取随机图片")
            return

        # 逐屏模式：为每块显示器分配不同图片；失败已由 set_wallpapers 内部
        # 回退为统一 SPI 设置并返回 False，这里把原因暴露给用户（不再静默失败）。
        if self._is_per_monitor_mode():
            if self._switch_per_monitor():
                logger.info("逐屏壁纸切换成功")
            else:
                logger.error("逐屏壁纸设置失败（已回退统一设置）")
                self.show_platform_warning(
                    "逐屏壁纸设置失败，已回退为统一壁纸"
                )
            return

        success = self._wallpaper_manager.set_wallpaper(image_path)
        if success:
            available = self._library.list_available()
            index = available.index(image_path) if image_path in available else 0
            self._update_preview(image_path, index)
            logger.info("壁纸切换成功: %s", image_path)
        else:
            logger.error("壁纸设置失败: %s", image_path)

    # ===== 逐屏壁纸（#7）=====

    def _is_per_monitor_mode(self) -> bool:
        """当前配置是否为逐屏模式且平台/COM 支持。"""
        if self._settings.get("wallpaper_mode", "unified") != "per_monitor":
            return False
        return bool(
            getattr(self._wallpaper_manager, "is_multi_monitor_supported", False)
        )

    def _build_per_monitor_map(self) -> dict:
        """为每块显示器各分配一张随机图片（尽量互不相同）。

        Returns:
            ``{monitor_id: image_path}``；无可用图片时返回 {}。
            可用图片不足显示器数时允许重复（记 debug 日志，不报错）。
        """
        monitors = self._wallpaper_manager.list_monitors()
        if not monitors:
            return {}
        available = self._library.list_available()
        if not available:
            return {}

        monitor_ids = [m["id"] for m in monitors]
        if len(available) >= len(monitor_ids):
            chosen = random.sample(available, len(monitor_ids))
        else:
            logger.debug(
                "可用图片(%d)少于显示器数(%d)，逐屏分配允许重复",
                len(available),
                len(monitor_ids),
            )
            chosen = [random.choice(available) for _ in monitor_ids]
        return dict(zip(monitor_ids, chosen))

    def _switch_per_monitor(self) -> bool:
        """按逐屏模式为各显示器分配并设置壁纸，成功则落盘 per_monitor_map。"""
        mapping = self._build_per_monitor_map()
        if not mapping:
            logger.warning("逐屏壁纸：无显示器或无可用图片")
            return False
        success = self._wallpaper_manager.set_wallpapers(mapping)
        if success:
            self._settings.set("per_monitor_map", mapping)
        return success

    def _apply_wallpaper_for_mode(self, image_path: str) -> bool:
        """调度器设置壁纸的分流器（注入给 Scheduler.set_wallpaper_applier）。

        逐屏模式 → 各显示器独立随机分配；统一模式 → 整屏设置。这样调度器无需
        直接调用 WallpaperManager，避免逐屏模式下"先统一设一张、再被覆盖"的双写。
        """
        if self._is_per_monitor_mode():
            return self._switch_per_monitor()
        return self._wallpaper_manager.set_wallpaper(image_path)

    def _update_multi_monitor_availability(self) -> None:
        """按平台/COM/显示器数启用或置灰「逐屏独立壁纸」开关。"""
        page = getattr(self, "_settings_page", None)
        if page is None:
            return
        supported = False
        monitor_count = 0
        try:
            supported = bool(
                getattr(self._wallpaper_manager, "is_multi_monitor_supported", False)
            )
            if supported:
                monitor_count = len(
                    list(self._wallpaper_manager.list_monitors() or [])
                )
        except Exception:
            logger.exception("探测逐屏壁纸能力失败")
            supported, monitor_count = False, 0

        if not supported:
            reason = "当前系统不支持逐屏壁纸（需要 Windows 且 IDesktopWallpaper 可用）"
        elif monitor_count < 2:
            reason = "当前只检测到 1 块显示器，逐屏壁纸无意义"
        else:
            reason = ""
        page.set_per_monitor_available(
            supported and monitor_count >= 2, reason
        )

    def _on_wallpaper_mode_toggled(self, checked: bool) -> None:
        """「逐屏独立壁纸」开关：保存模式；开启且可用时立即应用一次。"""
        mode = "per_monitor" if checked else "unified"
        self._settings.set("wallpaper_mode", mode)
        logger.info("壁纸模式切换为: %s", mode)
        if checked and self._is_per_monitor_mode():
            if not self._switch_per_monitor():
                self.show_platform_warning("逐屏壁纸应用失败，已回退为统一壁纸")

    def _persist_skip_favorites(self) -> None:
        """将当前 skip/favorites 集合批量写回配置（单次保存）。"""
        self._settings.set_many({
            "skip_list": self._library.skip_list,
            "favorites": self._library.favorites,
        })

    def _handle_skip(self) -> None:
        """处理用户点击'跳过'。"""
        if not self._preview_card._current_path:
            return
        self._library.skip(self._preview_card._current_path)
        logger.info("已跳过: %s", self._preview_card._current_path)
        self._persist_skip_favorites()



    def _on_top_status_click(self, event) -> None:
        """点击顶部状态栏，如果调度器暂停则恢复."""
        if self._scheduler is not None and not self._scheduler.is_active:
            self.resume_scheduler()
            self.update_top_status()
            logger.info("通过顶栏点击恢复了调度器")

    def _handle_favorite(self) -> None:
        """处理用户点击'收藏'。"""
        if not self._preview_card._current_path:
            return
        is_new_fav = self._library.favorite(self._preview_card._current_path)
        if is_new_fav:
            logger.info("已收藏: %s", self._preview_card._current_path)
        else:
            # It was already in fav_set (idempotent), try unfavorite
            self._library.unfavorite(self._preview_card._current_path)
            logger.info("取消收藏: %s", self._preview_card._current_path)
        self._persist_skip_favorites()
        gallery = getattr(self, "_gallery_page", None)
        if gallery is not None:
            gallery.refresh()
        self._sync_sidebar_action_state()

    @Slot(str)
    def on_wallpaper_switched(self, path: str) -> None:
        """调度器触发切换后的回调，更新 UI。"""
        try:
            available = self._library.list_available()
            index = available.index(path) if path in available else 0
            self._update_preview(path, index)
            self._update_info_cards()
            self.update_top_status()
            self.update_bottom_bar()
        except Exception:
            logger.exception("更新 UI 失败")

    def request_switch(self) -> None:
        """供托盘菜单调用的公共切换入口。"""
        self._handle_switch()

    def pause_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.pause()
        self._tray.update_pause_status(True)
        # #8：暂停后立即刷新顶栏/信息卡/时钟，让"已暂停"当帧可见（此前只更新托盘）
        self.update_top_status()
        self._update_info_cards()
        self._update_clock()
        logger.info("调度已暂停")

    def resume_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.resume()
        self._tray.update_pause_status(False)
        # #8：恢复后立即刷新，倒计时当帧恢复显示
        self.update_top_status()
        self._update_info_cards()
        self._update_clock()
        logger.info("调度已恢复")

    # ===== UI 更新 =====

    def _update_preview(self, path: str, index: int) -> None:
        """更新预览卡片和页面状态。"""
        self._current_image_index = index
        self._preview_card.update_preview(path, index, self._library.total_count)

        file_name = Path(path).stem
        self._tray.setToolTip(f"Wallpace -- {file_name}")

        self._sync_sidebar_action_state()
        self._update_info_cards()

    def _on_gallery_click(self, image_path: str) -> None:
        """User clicked a gallery thumbnail - update preview and set as wallpaper."""
        try:
            success = self._wallpaper_manager.set_wallpaper(image_path)
            if success:
                available = self._library.list_available()
                index = available.index(image_path) if image_path in available else 0
                self._update_preview(image_path, index)
                self._highlight_current_gallery_item(image_path)
            else:
                logger.error("壁纸设置失败: %s", image_path)
        except Exception:
            logger.exception("画廊点击处理失败")

    def _refresh_gallery(self) -> None:
        """刷新图片库：后台线程扫描，完成后回主线程更新 UI。

        scan() 是同步递归 IO，在主线程执行会阻塞事件循环导致界面"未响应"。
        这里只投递后台任务；UI 更新在 _on_scan_finished 中完成。
        """
        self._start_async_scan()

    # ===== 扫描状态 facade（#1：委托给 ScanController，保持既有入口兼容）=====

    @property
    def _scan_running(self) -> bool:
        """是否正在后台扫描（读写，兼容既有测试对 run 中守卫的断言）。"""
        return self._scan.running

    @_scan_running.setter
    def _scan_running(self, value: bool) -> None:
        self._scan.running = bool(value)

    @property
    def _scan_pending(self) -> bool:
        """扫描期间是否已排队一次补扫（读写，兼容既有测试）。"""
        return self._scan.pending

    @_scan_pending.setter
    def _scan_pending(self, value: bool) -> None:
        self._scan.pending = bool(value)

    def _start_async_scan(self) -> None:
        """投递一次后台扫描（委托 ScanController）。扫描进行中合并为一次补扫。"""
        self._scan.request_scan()

    @Slot()
    def _on_scan_started(self) -> None:
        """后台扫描开始回调（主线程）：进入扫描态 UI。"""
        self._set_scan_ui_state(True)

    def _on_dirs_changed(self) -> None:
        """监听的图片文件夹内容发生变化 → 触发一次后台重扫。

        防抖已在 DirectoryWatcher 内部处理（1.5s 内的多次事件合并为一次），
        这里复用与手动扫描相同的 _start_async_scan 守卫（_scan_running /
        _scan_pending），不会绕过防重入状态机。
        """
        logger.info("检测到图片文件夹变化，自动重新扫描")
        self._start_async_scan()

    @Slot(list)
    def _on_scan_finished(self, images: list) -> None:
        """扫描完成回调（主线程）。应用结果并恢复 UI 状态。"""
        self._scan_running = False
        try:
            self._apply_scan_results(images)
        except Exception:
            logger.exception("应用扫描结果失败")
        finally:
            self._set_scan_ui_state(False)
        if self._scan_pending:
            # 扫描期间目录/设置有变更，补一次最新扫描
            self._scan_pending = False
            self._start_async_scan()

    def _apply_scan_results(self, images: list) -> None:
        """根据扫描结果更新预览、缩略图和状态栏（必须在主线程调用）。"""
        if images:
            first = images[0]
            self._update_preview(first, 0)
            logger.info("刷新图片库: %d 张", len(images))
            self._refresh_gallery_thumbnails()
            # 同步左侧「图片库」整页（含收藏/已跳过筛选），避免扫描后页面不刷新
            gallery = getattr(self, "_gallery_page", None)
            if gallery is not None:
                gallery.refresh()
            if hasattr(self, "_empty_guide"):
                self._empty_guide.setVisible(False)
                self._preview_card.setVisible(True)
        else:
            logger.info("未发现可用图片")
            if hasattr(self, "_empty_guide"):
                self._empty_guide.setVisible(True)
                self._preview_card.setVisible(False)
        self._update_info_cards()
        self.update_bottom_bar()

    def _set_scan_ui_state(self, scanning: bool) -> None:
        """扫描期间禁用会触发扫描的入口，并在状态栏显示提示。"""
        # 设置页控件在扫描期间整体禁用（控件现归属于 SettingsPage）
        self._settings_page.set_controls_enabled(not scanning)
        label = getattr(self, "_bottom_notifier_label", None)
        if label is not None:
            if scanning:
                label.setText("⏳ 正在扫描图片库…")
            else:
                self.update_bottom_bar()

    def _refresh_gallery_thumbnails(self) -> None:
        """重建横向缩略图条（委托 ``GalleryStrip.reload``）。

        缩略图控件的清理/懒加载逻辑已提取到 GalleryStrip（内部沿用 P0-1 修复：
        退订走 ``image_loader.cancel()``，不依赖 ``Connection.disconnect()``）。
        这里只负责取数据、传入当前选中项，并刷新顶栏/底栏。
        """
        images = self._library.list_available()
        current_path = getattr(self._preview_card, "_current_path", "")
        self._gallery_strip.reload(list(images), current_path)

        # Update bottom and top status bars
        self.update_bottom_bar()
        self.update_top_status()

    def _sync_sidebar_action_state(self) -> None:
        """同步侧边栏收藏按钮状态到当前预览图片。"""
        if not hasattr(self, "_sidebar") or self._sidebar is None:
            return
        current_path = getattr(self._preview_card, "_current_path", "")
        if not current_path:
            return
        is_favorited = self._library.is_favorite(current_path)
        action_buttons = getattr(self._sidebar, "_action_buttons", [])
        if len(action_buttons) >= 3:
            action_buttons[2].set_active(is_favorited)

    def update_top_status(self) -> None:
        """Update the top bar status badge based on scheduler state."""
        if self._scheduler is None:
            self._top_status.setText("● 未启动")
            return
        mode = self._scheduler.mode
        if not self._scheduler.is_active:
            self._top_status.setText("● 已暂停")
        elif mode == "daily_random":
            next_switch = self._scheduler.get_next_switch_time()
            if next_switch:
                self._top_status.setText(
                    f"● 已启用 · 明天 {next_switch.strftime('%H:%M')} 切换"
                )
            else:
                self._top_status.setText("● 已启用 · 明天自动切换")
        elif mode == "interval_minutes":
            mins = self._scheduler.interval_minutes or 60
            self._top_status.setText(f"● 已启用 · 每 {mins} 分钟")
        elif mode == "manual":
            self._top_status.setText("● 已启用 · 手动模式")

    def _update_info_cards(self) -> None:
        """更新信息网格卡片的内容。"""
        mode_name = self._settings.get("switch_mode", "daily_random")
        mode_display = {
            "daily_random": "每日随机",
            "interval_minutes": f"每{self._settings.get('interval_minutes', 60)}分钟",
            "manual": "手动",
        }
        # 优先显示可用数量（排除跳过项），若为0则显示总数量
        total = self._library.available_count
        if total == 0:
            total = self._library.total_count
        next_text = self._get_next_switch_text() if total > 0 else "添加图片后"

        self._update_card_value(self._method_card, mode_display.get(mode_name, "每日随机"))
        self._update_card_value(self._next_time_card, next_text)
        self._update_card_value(self._count_card, str(total))

    @staticmethod
    def _update_card_value(card: QFrame, value: str) -> None:
        """更新信息卡片的值标签文本。"""
        # 找到卡片中的 QLabel（按顺序第一个就是 value 标签）
        labels = card.findChildren(QLabel)
        if labels:
            # 第一个是 label（小字），第二个是 value（大字）
            if len(labels) >= 2:
                labels[1].setText(value)
            elif len(labels) == 1:
                labels[0].setText(value)

    def _highlight_current_gallery_item(self, current_path: str) -> None:
        """Update the visual highlight of the currently active gallery item."""
        self._gallery_strip.set_current(current_path)

    def update_bottom_bar(self) -> None:
        """Update bottom status bar with current library info."""
        images = self._library.list_available()
        dirs = self._library._directories or ["未配置"]
        dir_text = ", ".join(dirs[:2])  # show first two dirs
        fav_count = len(self._library.favorites)
        enabled = "已启用" if self._scheduler and self._scheduler.is_active else "已暂停"
        icon_folder = "\U0001f4c2"  # 📂 folder
        self._bottom_source_label.setText(
            f"{icon_folder} {dir_text} ({len(images)}/{self._library.total_count})"
        )
        star_icon = "★"  # ★
        bell_icon = "\U0001f514"  # 🔔 bell
        self._bottom_fav_count.setText(f"{star_icon} {fav_count}")
        self._bottom_notifier_label.setText(f"{bell_icon} {enabled}")

    def open_settings(self) -> None:
        """切换到设置页面。"""
        logger.debug("打开设置页面")
        self._stacked.setCurrentIndex(3)
        self._set_active_button("settings")

    def _set_active_button(self, page_id: str) -> None:
        """供 SidebarNavigation 外部调用。"""
        if hasattr(self, "_sidebar"):
            self._sidebar.set_active_button(page_id)

    def _get_time_string(self) -> str:
        now = datetime.now()
        return now.strftime("%H:%M:%S")

    def _get_date_string(self) -> str:
        now = datetime.now()
        return now.strftime("%Y年%m月%d日")

    def closeEvent(self, event) -> None:
        """窗口关闭事件。

        若开启“关闭窗口时最小化到托盘”且非强制退出，则忽略关闭事件、
        隐藏窗口，保留托盘与调度器继续运行（类似微信）。否则真正退出。
        """
        minimize_to_tray = self._settings.get("minimize_to_tray", True)
        if minimize_to_tray and not self._force_quit:
            # 最小化到托盘：保留托盘与调度器，壁纸切换继续工作
            event.ignore()
            self.hide()
            if hasattr(self, "_tray") and self._tray is not None:
                self._tray.show()
                self._tray.setVisible(True)
            return

        # 真正退出：停调度器、停文件夹监听、隐藏并删除托盘
        if hasattr(self, "_scheduler") and self._scheduler is not None:
            self._scheduler.stop()
        if hasattr(self, "_watcher") and self._watcher is not None:
            # 停止 watchdog Observer 并 join，避免后台线程导致进程无法退出
            self._watcher.stop()
        # 停止在途扫描并等待线程池结束，避免 QThreadPool 析构阻塞退出（#1）
        if getattr(self, "_scan", None) is not None:
            self._scan.shutdown()
        # 释放逐屏壁纸的 COM 对象，避免泄漏（幂等，非 Windows 下为空操作）
        if getattr(self, "_wallpaper_manager", None) is not None:
            try:
                self._wallpaper_manager.release()
            except Exception:
                logger.exception("释放壁纸管理器 COM 资源失败")
        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.setVisible(False)
            self._tray.deleteLater()
        super().closeEvent(event)
        event.accept()

    def quit_app(self) -> None:
        """强制退出应用：绕过最小化到托盘逻辑，真正关闭窗口。"""
        self._force_quit = True
        self.close()
