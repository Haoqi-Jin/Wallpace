"""src/app/settings_controller.py — 设置页业务逻辑（#1 从 window.py 抽出）。

把一组内聚的「设置 → 注册表 / 调度器 / 目录」副作用从 MainWindow 抽出，
MainWindow 只保留同名 facade 方法转发到本控制器。

依赖注入策略
------------
- **稳定依赖**（``settings`` / ``library`` / ``settings_page`` / ``wallpaper_manager``）
  直接注入实例；
- **可能被替换的依赖** 用 provider 回调**延迟求值**：
  ``scheduler_provider``（既有测试会重新赋值 ``main_window._scheduler``）、
  ``scan_state``（扫描中守卫，读 ``main_window._scan_running``）；
- **跨层 UI 动作** 用回调注入：``refresh_gallery`` / ``refresh_status`` /
  ``set_watcher_directories`` / ``on_switch`` / ``autostart_manager_factory``，
  从而与 MainWindow 解耦且保持既有 monkeypatch 语义（例如
  ``src.app.window.AutostartManager`` 的替换须能被感知——故工厂用 lambda 延迟解析）。
"""

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFileDialog, QMessageBox

logger = logging.getLogger(__name__)


class SettingsController(QObject):
    """设置页业务逻辑：自启注册表、切换模式、间隔时间、图片目录增删。"""

    def __init__(
        self,
        settings,
        library,
        scheduler_provider: Callable[[], object | None],
        settings_page,
        wallpaper_manager,
        scan_state: Callable[[], bool],
        *,
        refresh_gallery: Callable[[], None],
        refresh_status: Callable[[], None],
        set_watcher_directories: Callable[[list], None],
        on_switch: Callable[[str], None] | None = None,
        autostart_manager_factory: Callable[[], object] | None = None,
    ) -> None:
        """注入依赖。

        Args:
            settings: 配置对象。
            library: 图片库。
            scheduler_provider: 返回当前调度器的回调（延迟求值，兼容测试替换）。
            settings_page: 设置页视图（供 refresh / set_* 使用）。
            wallpaper_manager: 壁纸管理器（预留：逐屏壁纸接线仍在 MainWindow）。
            scan_state: 返回"是否正在扫描"的回调（保留扫描中禁止改目录/模式的守卫）。
            refresh_gallery: 触发图片库刷新（MainWindow._refresh_gallery）。
            refresh_status: 刷新信息卡与顶栏（MainWindow._refresh_status）。
            set_watcher_directories: 同步文件夹监听目录。
            on_switch: 调度器触发切换时的 UI 回调（MainWindow.on_wallpaper_switched）。
            autostart_manager_factory: 返回 AutostartManager 实例的工厂（延迟解析，
                使 ``src.app.window.AutostartManager`` 的 monkeypatch 生效）。
        """
        super().__init__()
        self._settings = settings
        self._library = library
        self._scheduler_provider = scheduler_provider
        self._settings_page = settings_page
        self._wallpaper_manager = wallpaper_manager
        self._scan_state = scan_state
        self._refresh_gallery = refresh_gallery
        self._refresh_status = refresh_status
        self._set_watcher_directories = set_watcher_directories
        self._on_switch = on_switch
        self._autostart_manager_factory = autostart_manager_factory

    # ----- 内部辅助 -----

    @property
    def _scheduler(self):
        """每次调用都经 provider 取当前调度器（延迟求值）。"""
        return self._scheduler_provider()

    def _is_scanning(self) -> bool:
        """扫描进行中禁止变更目录 / 切换模式（避免扫描结果过期）。"""
        return bool(self._scan_state())

    # ----- 通用开关 -----

    def on_autostart_toggled(self, checked: bool) -> None:
        """“开机自启”开关：保存设置并同步注册表（经 AutostartManager）。"""
        self._settings.set("auto_start", bool(checked))
        if self._autostart_manager_factory is None:
            return
        manager = self._autostart_manager_factory()
        if checked:
            manager.enable()
        else:
            manager.disable()

    def on_minimize_toggled(self, checked: bool) -> None:
        """“关闭窗口时最小化到托盘”开关：仅保存设置。"""
        self._settings.set("minimize_to_tray", bool(checked))

    def on_uninstall_cleanup(self) -> None:
        """G02 卸载清理入口：移除开机自启项 + 删除 ~/.wallpace（配置/日志）。

        不可逆操作：先二次确认；确认后按「先自启项、后数据目录」顺序执行。
        数据目录删除失败不影响自启项移除结果（用户可手动删）。
        """
        reply = QMessageBox.question(
            self._settings_page,
            "卸载清理",
            "将移除开机自启项，并删除 ~/.wallpace 下的配置/收藏/日志。\n"
            "此操作不可撤销，确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._autostart_manager_factory is not None:
            manager = self._autostart_manager_factory()
            manager.disable()
        data_dir = Path.home() / ".wallpace"
        try:
            shutil.rmtree(data_dir)
            logger.info("已删除数据目录: %s", data_dir)
        except OSError as exc:
            logger.warning("删除数据目录失败（可手动删除）: %s", exc)

    def on_open_logs(self) -> None:
        """G07 诊断入口：在文件管理器中打开日志目录。

        打开失败时弹提示（而非静默失败）——入口存在的意义就是让用户
        在出问题时能自助定位日志，打不开就必须告知。
        """
        from src.core import diagnostics

        if not diagnostics.open_logs_directory():
            QMessageBox.critical(
                self._settings_page,
                "无法打开日志目录",
                f"请在文件管理器中手动打开：\n{diagnostics.LOGS_DIR}",
            )

    # ----- 图片目录 -----

    def add_directory(self) -> None:
        """打开文件夹选择对话框，添加到图片目录列表。"""
        if self._is_scanning():
            return
        folder = QFileDialog.getExistingDirectory(
            self._settings_page, "选择图片文件夹", ""
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
                self._set_watcher_directories(
                    self._settings.get("image_directories", [])
                )
                logger.info("已添加图片目录: %s", folder)
            else:
                logger.warning("添加图片目录失败（目录无效）: %s", folder)

    def remove_directory(self) -> None:
        """弹出选择框，让用户选择移除哪个文件夹。"""
        if self._is_scanning():
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
        self._set_watcher_directories(self._settings.get("image_directories", []))
        logger.info("已移除图片目录: %s", removed)

    def remove_single_dir(self, path: str) -> None:
        """移除指定路径的图片目录。"""
        if self._is_scanning():
            return
        reply = QMessageBox.question(
            self._settings_page,
            "确认删除",
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
                self._set_watcher_directories(
                    self._settings.get("image_directories", [])
                )
                logger.info("已移除图片目录: %s", path)

    # ----- 切换模式 / 间隔时间 -----

    def on_mode_changed(self, text: str) -> None:
        """处理模式切换。"""
        if self._is_scanning():
            # 扫描进行中模式选择器已被禁用，此分支仅是双保险
            return
        mode_display_map = {
            "daily_random": "每日随机",
            "interval_minutes": "间隔时间",
            "manual": "手动",
        }
        reverse_mode_map = {v: k for k, v in mode_display_map.items()}
        internal_mode = reverse_mode_map.get(text, "daily_random")

        # 收集本次要持久化的字段，最后一次性落盘
        # （原实现分两处 set，切到间隔模式时会写盘 2 次）
        updates = {"switch_mode": internal_mode}

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
            updates["interval_minutes"] = interval_val
            # 将已有的间隔值显示到输入框
            self._settings_page.set_interval_value(interval_val)
            # 同步调度器（走公开 setter，不再直写 _interval_minutes）
            scheduler = self._scheduler
            if scheduler is not None:
                try:
                    scheduler.set_interval_minutes(interval_val)
                except ValueError as exc:
                    logger.warning("同步间隔时间到调度器失败: %s", exc)
            self._settings_page.set_interval_visible(True)
        else:
            self._settings_page.set_interval_visible(False)

        # 单次落盘
        self._settings.set_many(updates)

        # 管理调度器
        scheduler = self._scheduler
        if scheduler is not None:
            if scheduler.is_running:
                scheduler.stop()
            scheduler.mode = internal_mode
            if internal_mode != "manual" and not scheduler.is_running:
                # 兜底：任何调度器异常都不能让设置页交互中断（P0-2 同源防护）
                try:
                    scheduler.start(on_switch=self._on_switch)
                except ValueError as exc:
                    logger.error("调度器启动失败，已降级为手动模式: %s", exc)
                    scheduler.mode = "manual"
                    scheduler.start(on_switch=self._on_switch)

        self._settings_page.update_mode_description(internal_mode)
        self._settings_page.refresh()

    def on_wallpaper_style_changed(self, text: str) -> None:
        """G09：壁纸填充方式变更：保存设置并立即应用到系统。

        Args:
            text: 下拉框显示的中文文案（如 "填满"）。
        """
        from src.app.pages.settings_page import WALLPAPER_STYLE_REVERSE_MAP
        style_key = WALLPAPER_STYLE_REVERSE_MAP.get(text, "fill")
        self._settings.set("wallpaper_style", style_key)
        self._wallpaper_manager.apply_wallpaper_style(style_key)

    def on_restore_defaults(self) -> None:
        """G11：恢复默认配置（不删文件，只重置配置值）。"""
        reply = QMessageBox.question(
            self._settings_page,
            "恢复默认配置",
            "将把所有设置恢复为出厂默认值（目录 / 收藏 / 跳过列表等清空）。\n"
            "此操作不会删除磁盘上的图片文件，确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._settings.reset_to_defaults()
        self._library._all_images = []
        self._library._skip_set = set()
        self._library._favorite_set = set()
        self._library._directories = []
        self._library._last_scan_signature = None
        self._refresh_gallery()
        self._settings_page.refresh()
        logger.info("配置已恢复为默认值")

    def on_export_config(self) -> None:
        """G11：导出配置 + 图库状态为 JSON 文件。"""
        import json as _json

        default_dir = str(Path.home() / "wallpace_export")
        file_path, _ = QFileDialog.getSaveFileName(
            self._settings_page,
            "导出配置",
            default_dir + ".json",
            "JSON 文件 (*.json)",
        )
        if not file_path:
            return
        data = {
            "version": 1,
            "config": self._settings._data,
            "image_library": self._library.to_dict(),
        }
        try:
            Path(file_path).write_text(
                _json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("配置已导出至: %s", file_path)
        except OSError as exc:
            QMessageBox.critical(
                self._settings_page, "导出失败", f"无法写入文件：\n{exc}"
            )

    def on_import_config(self) -> None:
        """G11：从 JSON 文件导入配置 + 图库状态。"""
        import json as _json
        from pathlib import Path

        file_path, _ = QFileDialog.getOpenFileName(
            self._settings_page,
            "导入配置",
            str(Path.home()),
            "JSON 文件 (*.json)",
        )
        if not file_path:
            return
        try:
            raw = _json.loads(Path(file_path).read_text(encoding="utf-8"))
            config = raw.get("config", {})
            lib_data = raw.get("image_library", {})

            if config:
                for key, val in config.items():
                    self._settings.set(key, val, defer=True)
                self._settings.save()

            if lib_data:
                self._library._directories = list(lib_data.get("directories", []))
                self._library._skip_set = set(lib_data.get("skip_list", []))
                self._library._favorite_set = set(lib_data.get("favorites", []))
                self._library._last_scan_signature = None

            self._refresh_gallery()
            self._settings_page.refresh()
            self._refresh_status()
            self._set_watcher_directories(
                self._settings.get("image_directories", [])
            )
            logger.info("配置已导入自: %s", file_path)
        except (OSError, _json.JSONDecodeError, KeyError) as exc:
            QMessageBox.critical(
                self._settings_page, "导入失败", f"无法解析文件：\n{exc}"
            )

    def apply_interval(self) -> None:
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
            scheduler = self._scheduler
            if scheduler is not None:
                # 走公开 setter：间隔模式运行中会立即重建定时器；暂停态下
                # 定时器本来就是停的，resume() 时会用新值重建（保持暂停语义，
                # 不再像旧实现那样 stop/start 把暂停状态清掉）。
                scheduler.set_interval_minutes(interval_val)
            logger.info("间隔时间已应用: %d 分钟", interval_val)
            self._refresh_status()
        except ValueError:
            logger.warning("间隔时间格式无效，请输入数字")
