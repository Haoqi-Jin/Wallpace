"""src/app/window.py — 主窗口 (B 方案: 图标侧边栏 + 粉蓝渐变)。

包含：顶栏、侧边导航、QStackedWidget 内容区、底部状态栏。
遵循 MVC 分离：UI 只负责展示和用户交互，业务逻辑委托给 core 层。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    Qt,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    Signal,
    Slot,
    QTimer,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
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
from src.core.autostart_registry import AutostartManager
from src.app.pages.gallery_page import GalleryPage
from src.app.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background library scan (QRunnable + QThreadPool)
# ---------------------------------------------------------------------------


class _ScanSignals(QObject):
    """后台扫描完成信号的载体（随 MainWindow 生命周期）。"""

    finished = Signal(list)  # list[str] — 扫描得到的图片路径


class _ScanJob(QRunnable):
    """后台扫描任务：只调用 ImageLibrary.scan() 纯数据操作，绝不触碰 QWidget。

    扫描结果通过 Qt 信号以队列连接（QueuedConnection）回到主线程，
    由 MainWindow._on_scan_finished 在主线程更新 UI。
    """

    def __init__(self, library: "ImageLibrary", signals: _ScanSignals) -> None:
        super().__init__()
        self._library = library
        self._signals = signals

    def run(self) -> None:
        try:
            images = self._library.scan()
        except Exception:
            logger.exception("后台扫描图片库失败")
            images = []
        try:
            self._signals.finished.emit(images)
        except RuntimeError:
            # 信号载体已随窗口销毁，静默丢弃结果
            pass


# ---------------------------------------------------------------------------
# Internal helper: clickable gallery thumbnail widget
# ---------------------------------------------------------------------------


class _GalleryThumbWidget(QLabel):
    """可点击的图片库缩略图，支持高亮当前选中项。

    缩略图通过 image_loader 在后台线程解码，避免在主线程加载 4K 大图造成卡顿。
    """

    clicked = Signal(str)  # emits image path on click
    _THUMB_SIZE = QSize(200, 160)

    def __init__(
        self,
        image_path: str,
        is_current: bool = False,
        parent: Optional[QWidget] = None,
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

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._image_path)
        super().mousePressEvent(event)


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

    # 缩略图懒加载参数：首批加载数量 + 距右端多少像素时预取下一批
    GALLERY_THUMB_BATCH = 10
    GALLERY_LOAD_AHEAD_PX = 240

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

        self._current_image_index = 0
        self._clock_timer: Optional["QTimer"] = None

        # --- 异步扫描基础设施 ---
        # 专用单线程池：保证任何时刻最多一个扫描任务，避免并发 scan。
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)
        self._scan_signals = _ScanSignals(self)
        self._scan_signals.finished.connect(self._on_scan_finished)
        self._scan_running = False
        self._scan_pending = False  # 扫描期间又有刷新请求时置位，完成后补一次

        # --- 缩略图懒加载状态 ---
        self._gallery_pending: list = []  # 尚未创建 widget 的图片路径
        self._gallery_items: list = []    # 已加载（已创建 widget）的图片路径

        # --- 文件夹变更监听（watchdog 递归监听 + 防抖后触发重扫） ---
        self._watcher = DirectoryWatcher(
            extensions=self._settings.get("extensions", []),
            parent=self,
        )
        self._watcher.changes_detected.connect(self._on_dirs_changed)
        self._watcher.set_directories(self._settings.get("image_directories", []))

        self._setup_window()
        self._setup_ui()
        self._start_clock_timer()

    def _setup_window(self) -> None:
        """窗口基本属性。"""
        self.setWindowTitle("Wallpace")
        self.setMinimumSize(800, 540)
        self.resize(960, 620)
        self.setWindowIcon(create_app_icon(256))
        logger.info("主窗口初始化完成")

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

        # Horizontal scroll area for thumbnails
        self._gallery_scroll = QScrollArea()
        self._gallery_scroll.setFixedHeight(100)
        self._gallery_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._gallery_scroll.setWidgetResizable(True)
        self._gallery_content = QWidget()
        self._gallery_layout = QHBoxLayout(self._gallery_content)
        self._gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_layout.setSpacing(8)
        self._gallery_scroll.setWidget(self._gallery_content)
        # 懒加载：滚动接近右端（或内容区范围变化）时按需追加下一批缩略图
        hbar = self._gallery_scroll.horizontalScrollBar()
        hbar.valueChanged.connect(self._maybe_load_more_thumbnails)
        hbar.rangeChanged.connect(
            lambda _min, _max: self._maybe_load_more_thumbnails()
        )
        gallery_layout.addWidget(self._gallery_scroll)

        layout.addWidget(gallery_section)

        return page

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
        """每秒更新时钟页面和顶部日期显示。"""
        now = datetime.now()
        if hasattr(self, "_clock_time_label"):
            self._clock_time_label.setText(now.strftime("%H:%M:%S"))
        if hasattr(self, "_clock_date_label"):
            self._clock_date_label.setText(now.strftime("%Y年%m月%d日  %A"))
        if hasattr(self, "_clock_next_label"):
            self._clock_next_label.setText(self._get_next_switch_text())

    def _get_next_switch_text(self) -> str:
        """获取下次切换时间的描述文本。"""
        if self._scheduler is None:
            return "调度器未启动"
        if not self._scheduler.is_active:
            return "● 调度已暂停"
        mode = self._scheduler.mode
        if mode == "daily_random":
            next_time = self._scheduler.get_next_switch_time()
            if next_time:
                delta = next_time - datetime.now()
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes = remainder // 60
                return f"下次切换: {hours}小时{minutes}分钟后 ({next_time.strftime('%H:%M')})"
            return "下次切换: 明天自动切换"
        elif mode == "interval_minutes":
            mins = self._scheduler.interval_minutes or 60
            return f"每 {mins} 分钟自动切换一次"
        elif mode == "manual":
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
        }
        page = SettingsPage(settings=self._settings, callbacks=callbacks)
        self._settings_page = page
        # 暴露复选框引用，保持既有测试/外部调用兼容
        self._autostart_check = page.autostart_check
        self._minimize_check = page.minimize_check
        return page

    # ===== 设置页面的辅助方法 =====

    def _on_autostart_toggled(self, checked: bool) -> None:
        """“开机自启”开关：保存设置并同步注册表（经 AutostartManager）。"""
        self._settings.set("auto_start", bool(checked))
        if checked:
            AutostartManager().enable()
        else:
            AutostartManager().disable()

    def _on_minimize_toggled(self, checked: bool) -> None:
        """“关闭窗口时最小化到托盘”开关：仅保存设置。"""
        self._settings.set("minimize_to_tray", bool(checked))

    # ===== 设置页面的辅助方法 =====

    def _add_directory(self) -> None:
        """打开文件夹选择对话框，添加到图片目录列表。"""
        if self._scan_running:
            # 扫描进行中禁止变更目录，避免扫描结果过期
            return
        folder = QFileDialog.getExistingDirectory(
            self, "选择图片文件夹", ""
        )
        if folder:
            dirs = self._settings.get("image_directories", [])
            if folder not in dirs:
                dirs.append(folder)
                self._settings.set("image_directories", dirs)
            # 关键修复：只将目录添加到 ImageLibrary，避免重复同步扫描。
            if self._library.add_directory(folder, scan=False):
                self._refresh_gallery()
                self._settings_page.refresh()
                self._watcher.set_directories(
                    self._settings.get("image_directories", [])
                )
                logger.info("已添加图片目录: %s", folder)
            else:
                logger.warning("添加图片目录失败（目录无效）: %s", folder)

    def _on_mode_changed(self, text: str) -> None:
        """处理模式切换。"""
        if self._scan_running:
            # 扫描进行中模式选择器已被禁用，此分支仅是双保险
            return
        mode_display_map = {
            "daily_random": "每日随机",
            "interval_minutes": "间隔时间",
            "manual": "手动",
        }
        reverse_mode_map = {v: k for k, v in mode_display_map.items()}
        internal_mode = reverse_mode_map.get(text, "daily_random")

        # 保存模式到 settings
        self._settings.set("switch_mode", internal_mode)

        # 如果是 interval_minutes 模式，从 settings 读取现有的间隔值
        if internal_mode == "interval_minutes":
            interval_val = self._settings.get("interval_minutes", 60)
            if not isinstance(interval_val, int) or interval_val <= 0:
                interval_val = 60
            # 关键修复（P0-2）：兜底值必须写回配置。
            # 早期实现只把兜底值给了 UI 和 scheduler，唯独没持久化，于是
            # 配置变成 {switch_mode: "interval_minutes", interval_minutes: null}，
            # 下次启动 main.py 构造 Scheduler(interval_minutes=None) →
            # start() 抛 ValueError → 打包成 --windowed 后表现为"双击无反应退出"。
            self._settings.set("interval_minutes", interval_val)
            # 将已有的间隔值显示到输入框
            self._settings_page.set_interval_value(interval_val)
            # 同步调度器（走公开 setter，不再直写 _interval_minutes）
            if self._scheduler is not None:
                try:
                    self._scheduler.set_interval_minutes(interval_val)
                except ValueError as exc:
                    logger.warning("同步间隔时间到调度器失败: %s", exc)
            self._settings_page.set_interval_visible(True)
        else:
            self._settings_page.set_interval_visible(False)

        # 管理调度器
        if self._scheduler is not None:
            if self._scheduler.is_running:
                self._scheduler.stop()
            self._scheduler.mode = internal_mode
            if internal_mode != "manual" and not self._scheduler.is_running:
                # 兜底：任何调度器异常都不能让设置页交互中断（P0-2 同源防护）
                try:
                    self._scheduler.start(on_switch=self.on_wallpaper_switched)
                except ValueError as exc:
                    logger.error("调度器启动失败，已降级为手动模式: %s", exc)
                    self._scheduler.mode = "manual"
                    self._scheduler.start(on_switch=self.on_wallpaper_switched)

        self._settings_page.update_mode_description(internal_mode)
        self._settings_page.refresh()

    def _apply_interval(self) -> None:
        """应用间隔时间设置。"""
        try:
            interval_val_str = self._settings_page.get_interval_text()
            if not interval_val_str:
                logger.warning("请输入间隔时间")
                return
            interval_val = int(interval_val_str)
            if interval_val <= 0:
                logger.warning("间隔时间必须为正数")
                return
            self._settings.set("interval_minutes", interval_val)
            if self._scheduler is not None:
                # 走公开 setter：间隔模式运行中会立即重建定时器；暂停态下
                # 定时器本来就是停的，resume() 时会用新值重建（保持暂停语义，
                # 不再像旧实现那样 stop/start 把暂停状态清掉）。
                self._scheduler.set_interval_minutes(interval_val)
            logger.info("间隔时间已应用: %d 分钟", interval_val)
            self._update_info_cards()
            self.update_top_status()
        except ValueError:
            logger.warning("间隔时间格式无效，请输入数字")

    def _remove_directory(self) -> None:
        """弹出选择框，让用户选择移除哪个文件夹。"""
        if self._scan_running:
            return
        dirs = self._settings.get("image_directories", [])
        if not dirs:
            return
        # 如果有多个目录，用简易方式：移除最后一个
        removed = dirs.pop()
        self._settings.set("image_directories", dirs)
        self._library.remove_directory(removed, scan=False)
        self._refresh_gallery()
        self._settings_page.refresh()
        self._watcher.set_directories(self._settings.get("image_directories", []))
        logger.info("已移除图片目录: %s", removed)

    def _remove_single_dir(self, path: str) -> None:
        """移除指定路径的图片目录。"""
        if self._scan_running:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要从壁纸库中移除「{path}」吗？\n（只是移除目录，不会删除文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            dirs = self._settings.get("image_directories", [])
            if path in dirs:
                dirs.remove(path)
                self._settings.set("image_directories", dirs)
                self._library.remove_directory(path, scan=False)
                self._refresh_gallery()
                self._settings_page.refresh()
                self._watcher.set_directories(
                    self._settings.get("image_directories", [])
                )
                logger.info("已移除图片目录: %s", path)

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

        success = self._wallpaper_manager.set_wallpaper(image_path)
        if success:
            available = self._library.list_available()
            index = available.index(image_path) if image_path in available else 0
            self._update_preview(image_path, index)
            logger.info("壁纸切换成功: %s", image_path)
        else:
            logger.error("壁纸设置失败: %s", image_path)

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
        logger.info("调度已暂停")

    def resume_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.resume()
        self._tray.update_pause_status(False)
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

    def _start_async_scan(self) -> None:
        """投递一次后台扫描。扫描进行中再次调用时合并为一次补扫。"""
        if self._scan_running:
            # 防重入：不并发多个 scan，标记待补扫即可
            self._scan_pending = True
            return
        self._scan_running = True
        self._set_scan_ui_state(True)
        self._scan_pool.start(_ScanJob(self._library, self._scan_signals))

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
        """重建缩略图区域：只立即加载首批，其余滚动到时懒加载。

        清理旧 widget 时先用 takeAt 把 item 从布局中摘除，再 cancel 订阅 +
        deleteLater。早期实现在这里调用 `widget.cleanup()`（内部用
        `QMetaObject.Connection.disconnect()`），而本机 PySide6 的 Connection
        没有该方法，会在第一个 widget 处抛 AttributeError，导致本方法整体中断、
        后续 `gallery.refresh()` / 信息卡 / 底栏刷新全部被跳过（P0-1）。
        现在退订走 image_loader.cancel()，不再依赖 disconnect()。
        """
        while self._gallery_layout.count():
            item = self._gallery_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is None:
                continue
            if isinstance(widget, _GalleryThumbWidget):
                image_loader.cancel(widget._image_path, widget._on_image_ready)
            widget.setParent(None)
            widget.deleteLater()

        images = self._library.list_available()
        self._gallery_pending = list(images)
        self._gallery_items = []
        self._gallery_scroll.horizontalScrollBar().setValue(0)
        self._append_gallery_batch()  # 首批 GALLERY_THUMB_BATCH 张

        # Update bottom and top status bars
        self.update_bottom_bar()
        self.update_top_status()

    def _append_gallery_batch(self) -> None:
        """从待加载队列取一批图片创建缩略图 widget（主线程）。"""
        batch = self._gallery_pending[: self.GALLERY_THUMB_BATCH]
        del self._gallery_pending[: len(batch)]
        if not batch:
            return
        current_path = getattr(self._preview_card, "_current_path", "")
        for img_path in batch:
            thumb = _GalleryThumbWidget(img_path, is_current=(img_path == current_path))
            thumb.clicked.connect(lambda p=img_path: self._on_gallery_click(p))
            self._gallery_layout.addWidget(thumb)
        self._gallery_items.extend(batch)

    @Slot()
    def _maybe_load_more_thumbnails(self, *args) -> None:  # noqa: ANN002
        """滚动接近右端时追加下一批缩略图（懒加载）。"""
        if not getattr(self, "_gallery_pending", None):
            return
        hbar = self._gallery_scroll.horizontalScrollBar()
        if hbar.maximum() - hbar.value() <= self.GALLERY_LOAD_AHEAD_PX:
            self._append_gallery_batch()

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
        for w_idx in range(self._gallery_layout.count()):
            widget = self._gallery_layout.itemAt(w_idx).widget()
            if isinstance(widget, _GalleryThumbWidget):
                widget.set_is_current(widget._image_path == current_path)

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
        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.setVisible(False)
            self._tray.deleteLater()
        super().closeEvent(event)
        event.accept()

    def quit_app(self) -> None:
        """强制退出应用：绕过最小化到托盘逻辑，真正关闭窗口。"""
        self._force_quit = True
        self.close()
