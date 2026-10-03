"""src/app/theme.py — 粉蓝渐变 QSS 主题引擎。

为整个应用提供统一的视觉风格：粉蓝渐变背景、圆角卡片、图标按钮等。
遵循 dev-standards.md 的配色约定。
"""


from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# ==================== 配色常量 ====================
COLOR_PINK_LIGHT = "#fce4ec"       # 极浅粉（卡片底色 / 背景装饰）
COLOR_PINK_MID = "#f8bbd0"         # 中粉（装饰）
COLOR_PINK_ACCENT = "#e91e63"      # 玫红（主强调色）

COLOR_BLUE_LIGHT = "#bbdefb"       # 极浅蓝（卡片底色）
COLOR_BLUE_MID = "#e3f2fd"         # 中蓝（高亮）
COLOR_BLUE_ACCENT = "#5c6bc0"      # 靛蓝（次要强调）
COLOR_BLUE_DARK = "#1a237e"        # 深蓝（文字标题）

COLOR_GRAY_50 = "#fafafa"          # 最浅灰（页面底色）
COLOR_GRAY_100 = "#f5f5f5"         # 浅灰（面板底色）
COLOR_GRAY_200 = "#eeeeee"         # 边线灰
COLOR_GRAY_500 = "#9e9e9e"         # 中灰（次文本）
COLOR_GRAY_800 = "#424242"         # 深灰（正文）

GRAY_COLORS: dict[str, str] = {
    "50": COLOR_GRAY_50,
    "100": COLOR_GRAY_100,
    "200": COLOR_GRAY_200,
    "500": COLOR_GRAY_500,
    "800": COLOR_GRAY_800,
}


class ThemeManager:
    """集中管理应用全局样式和字体设置。
    支持两种模式：
        - LightQSS: 仅轻量级 QSS 样式（兼容性好，渲染快）
        - Gradient: 渐变背景（效果更丰富，但需要 QPainter 配合）
    """

    MODE_LIGHT = "light_qss"
    MODE_GRADIENT = "gradient"

    def __init__(self) -> None:
        self._font_title: QFont | None = None

    # ==================== QSS 样式表 ====================

    @staticmethod
    def light_qss() -> str:
        """返回基础浅色 QSS 样式表。
        这是轻量级方案，覆盖全局控件、布局、滚动条等。
        不需要 QPainter 自定义绘制。
        """
        return (
            f"\n"
            f"/* ===== 全局基座 ===== */\n"
            f"QWidget {{\n"
            f"    font-family: 'Segoe UI', 'Microsoft YaHei UI', 'PingFang SC', sans-serif;\n"
            f"    color: {COLOR_GRAY_800};\n"
            f"    background-color: {COLOR_GRAY_50};\n"
            f"}}\n"
            f"/* 注意：不在 QMainWindow 上设置背景色，避免覆盖系统标题栏绘制 */\n"
            f"QLabel {{\n"
            f"    color: {COLOR_GRAY_800};\n"
            f"    font-size: 13px;\n"
            f"}}\n"
            f"QLabel[is-title='true'] {{\n"
            f"    font-size: 22px;\n"
            f"    font-weight: bold;\n"
            f"    color: {COLOR_BLUE_DARK};\n"
            f"}}\n"
            f"QLabel[is-subtitle='true'] {{\n"
            f"    font-size: 12px;\n"
            f"    color: {COLOR_GRAY_500};\n"
            f"}}\n"
            f"QLabel[is-stat-label='true'] {{\n"
            f"    font-size: 11px;\n"
            f"    color: {COLOR_GRAY_500};\n"
            f"    text-transform: uppercase;\n"
            f"    letter-spacing: 0.5px;\n"
            f"}}\n"
            f"QLabel[is-stat-value='true'] {{\n"
            f"    font-size: 16px;\n"
            f"    font-weight: 600;\n"
            f"    color: {COLOR_BLUE_DARK};\n"
            f"}}\n"
            f"QLabel#date-label {{\n"
            f"    font-size: 12px;\n"
            f"    color: {COLOR_GRAY_500};\n"
            f"}}\n"
            f"QLabel#preview-image {{\n"
            f"    border: none;\n"
            f"    background-color: transparent;\n"
            f"}}\n"
            f"QLabel#source-path {{\n"
            f"    font-size: 11px;\n"
            f"    color: rgba(255,255,255,0.8);\n"
            f"}}\n"
            f"\n"
            f"/* ===== 按钮通用 ===== */\n"
            f"QPushButton {{\n"
            f"    border: none;\n"
            f"    border-radius: 12px;\n"
            f"    padding: 10px 16px;\n"
            f"    font-size: 13px;\n"
            f"    font-weight: 600;\n"
            f"}}\n"
            f"QPushButton:disabled {{\n"
            f"    background-color: {COLOR_GRAY_200};\n"
            f"    color: {COLOR_GRAY_500};\n"
            f"}}\n"
            f"\n"
            f"/* ===== 卡片容器 ===== */\n"
            f"/* 只对特定 objectName 的 QFrame 应用无边框样式，避免影响系统组件 */\n"
            f"QFrame#top_bar {{\n"
            f"    border: none;\n"
            f"    border-radius: 0;\n"
            f"}}\n"
            f"QFrame#bottom_bar {{\n"
            f"    border: none;\n"
            f"    border-radius: 0;\n"
            f"}}\n"
            f"QFrame#info_grid {{\n"
            f"    border: none;\n"
            f"    border-radius: 0;\n"
            f"}}\n"
            f"QFrame#gallery-section {{\n"
            f"    border: none;\n"
            f"    border-radius: 0;\n"
            f"}}\n"
            f"QFrame#settings_group {{\n"
            f"    border: 1px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 8px;\n"
            f"    background-color: white;\n"
            f"}}\n"
            f"QFrame#info-card {{\n"
            f"    border: 1px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 10px;\n"
            f"}}\n"
            f"QGroupBox {{\n"
            f"    title-color: {COLOR_BLUE_DARK};\n"
            f"    border: 1px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 8px;\n"
            f"    margin-top: 12px;\n"
            f"    padding-top: 20px;\n"
            f"    font-weight: 600;\n"
            f"}}\n"
            f"QGroupBox::title {{\n"
            f"    subcontrol-origin: margin;\n"
            f"    left: 12px;\n"
            f"    padding: 0 6px;\n"
            f"}}\n"
            f"\n"
            f"/* ===== 滚动条 ===== */\n"
            f"QScrollBar:vertical {{\n"
            f"    width: 6px;\n"
            f"    background: transparent;\n"
            f"    margin: 0;\n"
            f"}}\n"
            f"QScrollBar::handle:vertical {{\n"
            f"    background: {COLOR_GRAY_200};\n"
            f"    border-radius: 3px;\n"
            f"    min-height: 30px;\n"
            f"}}\n"
            f"QScrollBar::handle:vertical:hover {{\n"
            f"    background: {COLOR_GRAY_500};\n"
            f"}}\n"
            f"QScrollBar::add-line, QScrollBar::sub-line {{\n"
            f"    height: 0;\n"
            f"}}\n"
            f"\n"
            f"/* ===== 输入框 ===== */\n"
            f"QLineEdit {{\n"
            f"    border: 1px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 8px;\n"
            f"    padding: 8px 12px;\n"
            f"    font-size: 13px;\n"
            f"    selection-background-color: {COLOR_PINK_MID};\n"
            f"}}\n"
            f"QLineEdit:focus {{\n"
            f"    border-color: {COLOR_BLUE_ACCENT};\n"
            f"}}\n"
            f"\n"
            f"/* ===== 复选框 ===== */\n"
            f"QCheckBox {{\n"
            f"    spacing: 8px;\n"
            f"    font-size: 13px;\n"
            f"}}\n"
            f"QCheckBox::indicator {{\n"
            f"    width: 18px;\n"
            f"    height: 18px;\n"
            f"    border: 2px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 4px;\n"
            f"}}\n"
            f"QCheckBox::indicator:checked {{\n"
            f"    background-color: {COLOR_BLUE_ACCENT};\n"
            f"    border-color: {COLOR_BLUE_ACCENT};\n"
            f"}}\n"
            f"\n"
            f"/* ===== 菜单 ===== */\n"
            f"QMenu {{\n"
            f"    border: 1px solid {COLOR_GRAY_200};\n"
            f"    border-radius: 8px;\n"
            f"    background-color: white;\n"
            f"    padding: 4px 0;\n"
            f"}}\n"
            f"QMenu::item {{\n"
            f"    padding: 8px 24px 8px 16px;\n"
            f"    font-size: 13px;\n"
            f"}}\n"
            f"QMenu::item:selected {{\n"
            f"    background-color: {COLOR_BLUE_LIGHT};\n"
            f"}}\n"
            f"QMenu::separator {{\n"
            f"    height: 1px;\n"
            f"    background-color: {COLOR_GRAY_200};\n"
            f"    margin: 4px 12px;\n"
            f"}}\n"
            f"\n"
            f"/* ===== 托盘 ===== */\n"
            f"QSystemTrayIcon {{\n"
            f"    background: transparent;\n"
            f"}}\n"
        )

    # ==================== 渐变顶栏 ====================

    @staticmethod
    def top_bar_gradient_css(top_color: str = COLOR_PINK_LIGHT,
                              bottom_color: str = COLOR_BLUE_LIGHT) -> str:
        """为顶栏组件生成 QLinearGradient 样式。
        Args:
            top_color: 顶部颜色（浅粉）。
            bottom_color: 底部颜色（浅蓝）。
        Returns:
            QSS 片段字符串。
        """
        return (
            f"background: qlineargradient("
            f"x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {top_color}, "
            f"stop:1 {bottom_color}"
            ");"
        )

    # ==================== 壁纸卡片渐变 ====================

    @staticmethod
    def wallpaper_card_bg_css() -> str:
        """壁纸预览卡片的渐变底背景。"""
        return (
            f"background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {COLOR_PINK_LIGHT}, "
            f"stop:1 {COLOR_BLUE_LIGHT}"
            ");"
        )

    # ==================== 信息网格卡片 ====================

    @staticmethod
    def info_card_css(highlight: bool = False) -> str:
        """信息网格卡片样式。
        Args:
            highlight: 是否使用渐变高亮样式。
        """
        if highlight:
            return (
                f"QFrame {{ "
                f"border-radius: 12px; "
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 {COLOR_PINK_LIGHT}, stop:1 {COLOR_BLUE_LIGHT}); "
                f"}}"
            )
        else:
            return (
                f"QFrame {{ "
                f"border-radius: 12px; "
                f"background-color: {COLOR_GRAY_100}; "
                f"border: 1px solid {COLOR_GRAY_200}; "
                f"}}"
            )

    # ==================== 字体加载 ====================

    def load_font(self, app: QApplication, family: str,
                   weight: int = QFont.Weight.Normal) -> None:
        """(预留) 加载自定义字体到 QFontDatabase。
        当前版本使用系统自带的中文字体，此处保留扩展接口。
        """
        pass
