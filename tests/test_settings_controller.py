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


# ==================== G09 壁纸填充方式 ====================


def test_wallpaper_style_changed_saves_and_applies() -> None:
    """G09：下拉框文案映射为内部值，存配置 + 调 wallpaper_manager。"""
    settings = MagicMock()
    settings.get.return_value = "fill"
    wm = MagicMock()
    ctrl = _make_controller_with_wm(settings, wm)

    ctrl.on_wallpaper_style_changed("完整显示（留黑边）")
    settings.set.assert_called_with("wallpaper_style", "fit")
    wm.apply_wallpaper_style.assert_called_once_with("fit")


def test_wallpaper_style_unknown_falls_back_to_fill() -> None:
    """未知文案（理论上 UI 不会发出）：兜底为 fill，不抛异常。"""
    settings = MagicMock()
    wm = MagicMock()
    ctrl = _make_controller_with_wm(settings, wm)

    ctrl.on_wallpaper_style_changed("不存在的文案")
    settings.set.assert_called_with("wallpaper_style", "fill")
    wm.apply_wallpaper_style.assert_called_once_with("fill")


def _make_controller_with_wm(settings=None, wm=None) -> SettingsController:
    return SettingsController(
        settings=settings or MagicMock(),
        library=MagicMock(),
        scheduler_provider=lambda: None,
        settings_page=MagicMock(),
        wallpaper_manager=wm or MagicMock(),
        scan_state=lambda: False,
        refresh_gallery=lambda: None,
        refresh_status=lambda: None,
        set_watcher_directories=lambda _dirs: None,
    )


# ==================== G11 配置导入 / 导出 / 恢复默认 ====================


def test_restore_defaults_reject_does_nothing() -> None:
    settings = MagicMock()
    lib = MagicMock()
    lib._all_images = ["/a.jpg"]
    ctrl = _make_controller_with_wm(settings, MagicMock())
    ctrl._library = lib

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        ctrl.on_restore_defaults()

    settings.reset_to_defaults.assert_not_called()
    assert lib._all_images == ["/a.jpg"]


def test_restore_defaults_yes_resets_settings_and_library() -> None:
    settings = MagicMock()
    lib = MagicMock()
    lib._all_images = ["/a.jpg"]
    lib._skip_set = {"/a.jpg"}
    lib._favorite_set = set()
    lib._directories = ["/pics"]
    ctrl = _make_controller_with_wm(settings, MagicMock())
    ctrl._library = lib

    with patch.object(
        QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
    ):
        ctrl.on_restore_defaults()

    settings.reset_to_defaults.assert_called_once()
    assert lib._all_images == []
    assert lib._skip_set == set()
    assert lib._directories == []


def test_export_config_writes_json(tmp_path) -> None:
    import json as _json

    settings = MagicMock()
    settings._data = {"switch_mode": "manual", "wallpaper_style": "fit"}
    lib = MagicMock()
    lib.to_dict.return_value = {"directories": ["/pics"], "skip_list": [], "favorites": []}
    wm = MagicMock()
    ctrl = _make_controller_with_wm(settings, wm)
    ctrl._library = lib

    target = tmp_path / "export.json"
    with (
        patch("src.app.settings_controller.Path.home", return_value=tmp_path),
        patch("src.app.settings_controller.QFileDialog.getSaveFileName", return_value=(str(target), "")),
    ):
        ctrl.on_export_config()

    assert target.exists()
    data = _json.loads(target.read_text(encoding="utf-8"))
    assert data["config"]["wallpaper_style"] == "fit"
    assert data["image_library"]["directories"] == ["/pics"]


def test_import_config_applies_settings_and_library(tmp_path) -> None:
    import json as _json

    src_file = tmp_path / "import.json"
    src_file.write_text(
        _json.dumps(
            {"config": {"switch_mode": "manual"}, "image_library": {"directories": ["/new"], "skip_list": ["/x"], "favorites": ["/y"]}}
        ),
        encoding="utf-8",
    )
    settings = MagicMock()
    lib = MagicMock()
    ctrl = _make_controller_with_wm(settings, MagicMock())
    ctrl._library = lib

    with patch("src.app.settings_controller.QFileDialog.getOpenFileName", return_value=(str(src_file), "")):
        ctrl.on_import_config()

    settings.set.assert_called()
    settings.save.assert_called()
    assert lib._directories == ["/new"]
    assert lib._skip_set == {"/x"}
    assert lib._favorite_set == {"/y"}


def test_import_corrupt_file_shows_error(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    ctrl = _make_controller_with_wm(MagicMock(), MagicMock())

    with (
        patch("src.app.settings_controller.QFileDialog.getOpenFileName", return_value=(str(bad), "")),
        patch.object(QMessageBox, "critical") as mock_crit,
    ):
        ctrl.on_import_config()

    mock_crit.assert_called_once()
