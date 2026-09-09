"""tests/test_window.py — MainWindow 集成测试。

测试主窗口的关键交互逻辑，使用 offscreen 平台避免 GUI 依赖。

注意：图片库扫描已改为后台线程异步执行（修复 UI 卡死），
依赖扫描结果的断言需先通过 _wait_for_scan() 等待扫描完成。
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QCloseEvent, QImage
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _wait_for_scan(window, timeout: float = 5.0) -> None:
    """等待 MainWindow 的后台扫描完成（驱动事件循环以投递完成信号）。"""
    deadline = time.time() + timeout
    while window._scan_running and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


@pytest.fixture
def app():
    """确保有 QApplication 实例。"""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not QApplication.instance():
        QApplication([])
    yield


@pytest.fixture
def main_window(app, tmp_config_dir):
    """创建 MainWindow 实例（配置隔离到临时目录，避免污染真实数据）。"""
    from src.core.settings import Settings
    from src.core.image_library import ImageLibrary
    from src.core.wallpaper_manager import WallpaperManager
    from src.core.scheduler import Scheduler
    from src.app.window import MainWindow

    settings = Settings()
    library = ImageLibrary(directories=[], extensions=set())
    wm = WallpaperManager()
    scheduler = Scheduler(mode="manual")
    scheduler.set_dependencies(library, wm)

    window = MainWindow(
        settings=settings,
        library=library,
        wallpaper_manager=wm,
        scheduler=scheduler,
    )
    yield window
    # 强制退出，确保窗口/托盘/调度器被真正清理（绕过最小化到托盘逻辑）
    window.quit_app()


class TestMainWindowInit:
    """测试主窗口初始化。"""

    def test_window_created(self, main_window):
        assert main_window is not None

    def test_window_title(self, main_window):
        assert main_window.windowTitle() == "Wallpace"

    def test_minimum_size(self, main_window):
        assert main_window.minimumWidth() == 800
        assert main_window.minimumHeight() == 540

    def test_initial_size(self, main_window):
        assert main_window.width() == 960
        assert main_window.height() == 620


class TestMainWindowPages:
    """测试页面导航。"""

    def test_has_preview_page(self, main_window):
        assert hasattr(main_window, "_preview_page")
        assert main_window._preview_page is not None

    def test_has_settings_page(self, main_window):
        assert hasattr(main_window, "_stacked")

    def test_current_page_is_preview(self, main_window):
        assert main_window._stacked.currentWidget() == main_window._preview_page


class TestMainWindowGallery:
    """测试图片库相关功能。"""

    def test_refresh_gallery_empty(self, main_window):
        """测试空图片库刷新。"""
        main_window._refresh_gallery()
        # 应该显示空状态引导
        assert hasattr(main_window, "_empty_guide")

    def test_refresh_gallery_with_images(self, main_window, tmp_path):
        """测试有图片时刷新。"""
        from PySide6.QtGui import QImageWriter
        from src.core.image_library import ImageLibrary

        # 创建测试图片
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        img_path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(img_path), "JPG")

        # 添加到 library
        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        assert main_window._library.total_count > 0

    def test_gallery_thumbnails_refresh(self, main_window, tmp_path):
        """测试缩略图刷新。"""
        from PySide6.QtGui import QImageWriter

        # 创建多张测试图片
        for i in range(5):
            img = QImage(100, 100, QImage.Format_RGB32)
            img.fill(i * 50)
            path = tmp_path / f"test_{i}.jpg"
            QImageWriter.write(img, str(path), "JPG")

        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        # 应该有缩略图 widget
        assert main_window._gallery_layout.count() > 0


class TestMainWindowSettings:
    """测试设置页面功能。"""

    def test_open_settings(self, main_window):
        """测试打开设置页面。"""
        main_window.open_settings()
        assert main_window._stacked.currentWidget() is not None

    def test_add_directory(self, main_window, tmp_path):
        """测试添加目录。"""
        from PySide6.QtGui import QImageWriter
 
        # 创建测试图片
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        img_path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(img_path), "JPG")
 
        # 模拟添加目录（绕过 QFileDialog）
        dirs = main_window._settings.get("image_directories", [])
        dirs.append(str(tmp_path))
        main_window._settings.set("image_directories", dirs)
        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        assert main_window._library.directory_count > 0
        assert main_window._library.total_count > 0

    def test_add_directory_does_not_double_scan(self, main_window, tmp_path, monkeypatch):
        """添加目录时应仅在 MainWindow 内部触发一次扫描。"""
        # 等待初始化时的后台扫描结束（扫描进行中 _add_directory 会被忽略）
        _wait_for_scan(main_window)
        monkeypatch.setattr(
            "src.app.window.QFileDialog.getExistingDirectory",
            lambda *args, **kwargs: str(tmp_path),
        )

        scan_flag = {}

        def fake_add_directory(path, scan=True):
            scan_flag["scan"] = scan
            return True

        monkeypatch.setattr(main_window._library, "add_directory", fake_add_directory)
        main_window._add_directory()

        assert scan_flag.get("scan") is False

    def test_remove_directory(self, main_window, tmp_path):
        """测试移除目录。"""
        from PySide6.QtGui import QImageWriter

        # 先添加目录
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        img_path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(img_path), "JPG")

        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        # 移除目录
        removed = main_window._library.remove_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        assert removed is True
        assert main_window._library.directory_count == 0


class TestMainWindowScheduler:
    """测试调度器相关功能。"""

    def test_scheduler_started(self, main_window):
        """测试调度器启动。"""
        assert main_window._scheduler is not None
        assert main_window._scheduler.mode == "manual"

    def test_pause_scheduler(self, main_window):
        """测试暂停调度器。"""
        main_window.pause_scheduler()
        assert not main_window._scheduler.is_active

    def test_resume_scheduler(self, main_window):
        """测试恢复调度器。"""
        # manual 模式调度器默认 is_running=False
        # 需要先启动再暂停才能恢复
        main_window._scheduler.start()
        main_window.pause_scheduler()
        main_window.resume_scheduler()
        assert main_window._scheduler.is_running


class TestP0Regressions:
    """P0 缺陷回归测试（修复后新增，防止回退）。"""

    def test_second_thumbnail_rebuild_does_not_raise(self, main_window, tmp_path):
        """P0-1：第二次重建缩略图条不得抛异常，也不得无限膨胀。

        修复前 _refresh_gallery_thumbnails() 里的 widget.cleanup() 会因
        QMetaObject.Connection 没有 disconnect() 而抛 AttributeError，
        导致本方法整体中断（旧缩略图不移除 + 后续刷新全被跳过）。
        """
        from PySide6.QtGui import QImageWriter

        for i in range(3):
            img = QImage(100, 100, QImage.Format_RGB32)
            img.fill(i * 50)
            QImageWriter.write(img, str(tmp_path / f"p0_{i}.jpg"), "JPG")

        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)
        first_count = main_window._gallery_layout.count()

        # 第二次重建：修复前这里会抛 AttributeError（被 _on_scan_finished 吞掉）
        main_window._refresh_gallery()
        _wait_for_scan(main_window)

        assert main_window._gallery_layout.count() == first_count
        assert first_count > 0

    def test_repeated_rebuild_keeps_widget_count_stable(
        self, main_window, tmp_path
    ):
        """P0-1：连续多次重建后缩略图数量保持稳定（修复前会持续累加）。"""
        from PySide6.QtGui import QImageWriter

        for i in range(12):
            img = QImage(100, 100, QImage.Format_RGB32)
            img.fill(i * 20)
            QImageWriter.write(img, str(tmp_path / f"stable_{i}.jpg"), "JPG")

        main_window._library.add_directory(str(tmp_path))
        main_window._refresh_gallery()
        _wait_for_scan(main_window)
        baseline = main_window._gallery_layout.count()

        for _ in range(3):
            main_window._refresh_gallery_thumbnails()
            QApplication.processEvents()

        assert main_window._gallery_layout.count() == baseline

    def test_mode_switch_persists_interval_minutes(self, main_window):
        """P0-2：切到「间隔时间」后 interval_minutes 必须被持久化。

        修复前兜底出的 60 只给了 UI 和 scheduler，配置里仍是 null，
        下次启动 Scheduler.start() 抛 ValueError → 打包后无声退出。
        """
        _wait_for_scan(main_window)  # 扫描进行中 _on_mode_changed 会直接 return
        main_window._settings.set("interval_minutes", None)
        main_window._on_mode_changed("间隔时间")

        assert main_window._settings.get("switch_mode") == "interval_minutes"
        assert main_window._settings.get("interval_minutes") == 60

    def test_restart_with_persisted_interval_does_not_crash(
        self, main_window, tmp_path
    ):
        """P0-2 端到端：按持久化配置重建 Scheduler 并 start() 不得抛异常。"""
        _wait_for_scan(main_window)
        main_window._settings.set("interval_minutes", None)
        main_window._on_mode_changed("间隔时间")

        # 模拟下次启动：完全按配置重建 Scheduler
        from src.core.scheduler import Scheduler

        restarted = Scheduler(
            mode=main_window._settings.get("switch_mode", "daily_random"),
            daily_time=main_window._settings.get("daily_time", "08:00"),
            interval_minutes=main_window._settings.get("interval_minutes", None),
        )
        restarted.set_dependencies(main_window._library, main_window._wallpaper_manager)
        restarted.start(on_switch=lambda _p: None)  # 修复前这里抛 ValueError
        assert restarted.is_running
        restarted.stop()

    def test_mode_switch_with_invalid_interval_degrades_to_manual(
        self, main_window
    ):
        """P0-2 兜底：即便调度器启动失败，也不得让模式切换流程中断。"""
        from src.core.scheduler import Scheduler

        broken = Scheduler(mode="manual")
        broken.set_dependencies(
            main_window._library, main_window._wallpaper_manager
        )
        main_window._scheduler = broken

        _wait_for_scan(main_window)
        main_window._settings.set("interval_minutes", None)
        main_window._on_mode_changed("间隔时间")

        # 兜底值已落盘，调度器处于可用状态
        assert main_window._settings.get("interval_minutes") == 60
        assert main_window._scheduler.mode == "interval_minutes"
        main_window._scheduler.stop()


class TestDirectoryWatcherWiring:
    """文件夹监听（DirectoryWatcher）在 MainWindow 里的接线。"""

    def test_watcher_is_constructed(self, main_window):
        assert hasattr(main_window, "_watcher")
        assert main_window._watcher is not None

    def test_on_dirs_changed_triggers_async_scan(self, main_window, monkeypatch):
        calls = []
        monkeypatch.setattr(main_window, "_start_async_scan", lambda: calls.append(1))
        main_window._on_dirs_changed()
        assert calls == [1]

    def test_on_dirs_changed_respects_scan_guard(self, main_window):
        """扫描进行中时 watcher 触发应合并为一次补扫，而非并发扫描。"""
        main_window._scan_running = True
        main_window._scan_pending = False
        try:
            main_window._on_dirs_changed()
            assert main_window._scan_pending is True
        finally:
            main_window._scan_running = False
            main_window._scan_pending = False

    def test_add_directory_syncs_watcher(self, main_window, tmp_path, monkeypatch):
        _wait_for_scan(main_window)
        monkeypatch.setattr(
            "src.app.window.QFileDialog.getExistingDirectory",
            lambda *args, **kwargs: str(tmp_path),
        )
        recorded = []
        monkeypatch.setattr(
            main_window._watcher,
            "set_directories",
            lambda dirs: recorded.append(list(dirs)),
        )
        main_window._add_directory()
        assert recorded
        assert str(tmp_path) in recorded[-1]

    def test_remove_single_dir_syncs_watcher(self, main_window, tmp_path, monkeypatch):
        _wait_for_scan(main_window)
        main_window._settings.set("image_directories", [str(tmp_path)])
        main_window._library.add_directory(str(tmp_path))
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **k: 16384,  # StandardButton.Yes
        )
        recorded = []
        monkeypatch.setattr(
            main_window._watcher,
            "set_directories",
            lambda dirs: recorded.append(list(dirs)),
        )
        main_window._remove_single_dir(str(tmp_path))
        assert recorded
        assert str(tmp_path) not in recorded[-1]


class TestMainWindowCloseBehavior:
    """关闭窗口行为：最小化到托盘 vs 真正退出。

    使用 tmp_config_dir 隔离配置，避免读写真实 .wallpace.json 造成用例间污染。
    """

    def test_close_minimizes_to_tray_by_default(
        self, tmp_config_dir, main_window, monkeypatch
    ):
        """默认开启最小化到托盘：忽略关闭事件、隐藏窗口、保留托盘与调度器。"""
        stopped = {"called": False}

        def fake_stop() -> None:
            stopped["called"] = True

        monkeypatch.setattr(main_window._scheduler, "stop", fake_stop)

        event = QCloseEvent()
        main_window.closeEvent(event)

        # 事件被忽略（isAccepted 为 False）
        assert event.isAccepted() is False
        # 窗口被隐藏
        assert main_window.isHidden() is True
        # 调度器未被停止（壁纸切换继续工作）
        assert stopped["called"] is False
        # 托盘仍然存在
        assert main_window._tray is not None

    def test_close_quits_when_minimize_disabled(
        self, tmp_config_dir, main_window, monkeypatch
    ):
        """关闭最小化到托盘关闭：真正退出，事件被接受且调度器停止。"""
        main_window._settings.set("minimize_to_tray", False)
        stopped = {"called": False}

        def fake_stop() -> None:
            stopped["called"] = True

        monkeypatch.setattr(main_window._scheduler, "stop", fake_stop)

        event = QCloseEvent()
        main_window.closeEvent(event)

        assert event.isAccepted() is True
        assert stopped["called"] is True

    def test_quit_app_forces_close(self, tmp_config_dir, main_window, monkeypatch):
        """quit_app() 绕过最小化逻辑，强制真正退出。"""
        stopped = {"called": False}

        def fake_stop() -> None:
            stopped["called"] = True

        monkeypatch.setattr(main_window._scheduler, "stop", fake_stop)

        main_window.quit_app()

        assert main_window._force_quit is True
        assert stopped["called"] is True


class TestMainWindowSettingsAutostart:
    """设置页“开机自启”与“最小化到托盘”开关。

    使用 tmp_config_dir 隔离配置，并 mock AutostartManager 避免真实写注册表。
    """

    def test_settings_has_autostart_checkbox(self, tmp_config_dir, main_window):
        """设置页应含“开机自启”与“关闭窗口时最小化到托盘”复选框。"""
        assert hasattr(main_window, "_autostart_check")
        assert hasattr(main_window, "_minimize_check")
        # 默认开启 -> 复选框勾选
        assert main_window._autostart_check.isChecked() is True
        assert main_window._minimize_check.isChecked() is True

    def test_autostart_toggle_enables(
        self, tmp_config_dir, main_window, monkeypatch
    ):
        """勾选“开机自启”：保存设置并调用 AutostartManager.enable()。"""
        mock_mgr = MagicMock()
        monkeypatch.setattr(
            "src.app.window.AutostartManager", lambda: mock_mgr
        )

        main_window._on_autostart_toggled(True)

        assert main_window._settings.get("auto_start") is True
        mock_mgr.enable.assert_called_once()
        mock_mgr.disable.assert_not_called()

    def test_autostart_toggle_disables(
        self, tmp_config_dir, main_window, monkeypatch
    ):
        """取消“开机自启”：保存设置并调用 AutostartManager.disable()。"""
        mock_mgr = MagicMock()
        monkeypatch.setattr(
            "src.app.window.AutostartManager", lambda: mock_mgr
        )

        main_window._on_autostart_toggled(False)

        assert main_window._settings.get("auto_start") is False
        mock_mgr.disable.assert_called_once()
        mock_mgr.enable.assert_not_called()

    def test_minimize_toggle_updates_settings(self, tmp_config_dir, main_window):
        """“关闭窗口时最小化到托盘”开关只保存设置。"""
        main_window._on_minimize_toggled(False)
        assert main_window._settings.get("minimize_to_tray") is False
        main_window._on_minimize_toggled(True)
        assert main_window._settings.get("minimize_to_tray") is True
