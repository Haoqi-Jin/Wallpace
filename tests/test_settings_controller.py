"""tests/test_settings_controller.py — 设置页控制器（SettingsController）测试。

覆盖 G02 卸载清理入口：二次确认拒绝时不动作；确认通过后
「先移除开机自启项、后删数据目录」。
"""

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from src.app.settings_controller import SettingsController


def _make_controller(cleanup_factory=None) -> SettingsController:
    """构造最小 SettingsController：依赖全用替身，避免触碰真实注册表。"""
    return SettingsController(
        settings=MagicMock(),
        library=MagicMock(),
        scheduler_provider=lambda: None,
        settings_page=MagicMock(),
        wallpaper_manager=MagicMock(),
        scan_state=lambda: False,
        refresh_gallery=lambda: None,
        refresh_status=lambda: None,
        set_watcher_directories=lambda _dirs: None,
        autostart_manager_factory=cleanup_factory,
    )


def test_cleanup_confirm_rejects_does_nothing(tmp_path) -> None:
    manager = MagicMock()
    ctrl = _make_controller(lambda: manager)
    data_dir = tmp_path / ".wallpace"
    data_dir.mkdir()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        ctrl.on_uninstall_cleanup()

    manager.disable.assert_not_called()
    assert data_dir.exists()  # 拒绝确认 → 数据目录原样保留


def test_cleanup_confirm_yes_removes_autostart_and_data(tmp_path) -> None:
    manager = MagicMock()
    ctrl = _make_controller(lambda: manager)
    fake_home = tmp_path / "home"
    data_dir = fake_home / ".wallpace"
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text("{}", encoding="utf-8")

    with (
        patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        patch("src.app.settings_controller.Path.home", return_value=fake_home),
    ):
        ctrl.on_uninstall_cleanup()

    manager.disable.assert_called_once()
    assert not data_dir.exists()  # 确认通过 → 数据目录被删除


def test_cleanup_without_factory_still_removes_data(tmp_path) -> None:
    """工厂为 None（非 Windows / 测试替身）时：跳过自启项，仍删数据目录。"""
    ctrl = _make_controller(cleanup_factory=None)
    fake_home = tmp_path / "home2"
    data_dir = fake_home / ".wallpace"
    data_dir.mkdir(parents=True)

    with (
        patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        patch("src.app.settings_controller.Path.home", return_value=fake_home),
    ):
        ctrl.on_uninstall_cleanup()

    assert not data_dir.exists()
