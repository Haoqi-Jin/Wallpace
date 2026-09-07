"""tests/test_settings.py — Settings 模块测试。

覆盖：加载、保存、get/set、重置、验证、JSON 解析错误处理。
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from core.settings import DEFAULT_CONFIG, Settings


class TestSettingsLoadSave:
    """配置文件的读取与写入。"""

    def test_default_when_no_file(self, tmp_config_dir: Path):
        s = Settings()
        assert isinstance(s.get("switch_mode"), str)

    def test_save_and_reload(self, tmp_config_dir: Path):
        s = Settings()
        s.set("custom_key", "custom_value")
        config_path = s.config_path
        assert config_path.exists()

        s2 = Settings(config_path=config_path)
        assert s2.get("custom_key") == "custom_value"

    def test_reset_to_defaults(self, tmp_config_dir: Path):
        s = Settings()
        s.set("custom_key", "value")
        result = s.reset_to_defaults()
        assert "custom_key" not in result
        assert result["switch_mode"] == "daily_random"

    def test_save_returns_none_not_data(self, tmp_config_dir: Path):
        s = Settings()
        assert s.save() is None

    def test_load_merges_invalid_config_with_defaults(self, tmp_config_dir: Path):
        config_path = tmp_config_dir / ".wallpace.json"
        config_path.write_text(
            '{"switch_mode": "invalid_mode", "interval_minutes": "oops", "enable_notifications": "no"}',
            encoding="utf-8",
        )

        s = Settings(config_path=config_path)

        assert s.get("switch_mode") == "daily_random"
        assert s.get("interval_minutes") is None
        assert s.get("enable_notifications") is True


class TestSettingsValidation:
    """validate() 静态方法的各种非法输入。"""

    def test_valid_empty(self):
        assert Settings.validate(DEFAULT_CONFIG) == []

    def test_invalid_image_directories_not_list(self):
        data = dict(DEFAULT_CONFIG)
        data["image_directories"] = "/not/a/list"
        errs = Settings.validate(data)
        assert any("必须是列表" in e for e in errs)

    def test_invalid_image_directories_empty_string(self):
        data = dict(DEFAULT_CONFIG)
        data["image_directories"] = [""]
        errs = Settings.validate(data)
        assert any("不能为空" in e for e in errs)

    def test_valid_nonexistent_directory_is_ok(self):
        """非存在目录不应导致校验失败——只检查格式，不检查路径是否存在。"""
        data = dict(DEFAULT_CONFIG)
        data["image_directories"] = ["/tmp/wallpace_test_images"]
        errs = Settings.validate(data)
        # Should pass because we no longer check path existence
        assert all("无效" not in e and "文件夹" not in e for e in errs)

    def test_invalid_switch_mode(self):
        data = {"switch_mode": "invalid_mode"}
        errs = Settings.validate(data)
        assert any("必须为" in e for e in errs)

    def test_invalid_daily_time(self):
        data = {**DEFAULT_CONFIG, "switch_mode": "daily_random", "daily_time": "25:99"}
        errs = Settings.validate(data)
        assert any("超出" in e or "HH:MM" in e for e in errs)

    def test_invalid_interval(self):
        data = {**DEFAULT_CONFIG, "interval_minutes": -5}
        errs = Settings.validate(data)
        assert any("正整数" in e for e in errs)

    def test_invalid_skip_list_type(self):
        data = {"skip_list": "not_a_list"}
        errs = Settings.validate(data)
        assert any("'skip_list'" in e for e in errs)


class TestSettingsSetGet:
    """get() 和 set() 基本操作。"""

    def test_get_default_missing(self):
        s = Settings()
        assert s.get("nonexistent_key") is None

    def test_set_and_get(self, tmp_config_dir: Path):
        config_path = tmp_config_dir / ".wallpace.json"
        s = Settings(config_path=config_path)
        s.set("my_test_key", 12345)
        assert s.get("my_test_key") == 12345

    def test_raw_is_copy(self):
        s = Settings()
        raw1 = s.raw
        raw2 = s.raw
        assert raw1 is not raw2


class TestSettingsCorruptedFile:
    """配置文件损坏时的降级行为。"""

    def test_corrupt_json_reverts_to_default(self, tmp_config_dir: Path):
        config_path = tmp_config_dir / ".wallpace.json"
        config_path.write_text("{not valid json!!!")
        s = Settings(config_path=config_path)
        assert s.get("switch_mode") == "daily_random"


class TestLegacyMigrationPaths:
    """P0-4 附带修复：迁移路径必须同时覆盖两种历史拼写。

    早期 LEGACY_CONFIG_PATHS 只列了错误拼写 `.wallspace.json`，
    存了正确拼写 `.wallpace.json` 的用户配置读不到。
    """

    @staticmethod
    def _module():
        """返回 Settings 实际所属模块对象。

        测试文件以 `core.settings` 风格导入，而运行时可能是 `src.core.settings`，
        两者在 sys.modules 中是不同对象，必须取 Settings.__module__ 才改得准。
        """
        import sys

        return sys.modules[Settings.__module__]

    def test_both_spellings_are_registered(self):
        mod = self._module()
        names = {p.name for p in mod.LEGACY_CONFIG_PATHS}
        assert ".wallpace.json" in names
        assert ".wallspace.json" in names

    def test_migration_reads_correct_spelling(self, tmp_path, monkeypatch):
        mod = self._module()

        target = tmp_path / "new" / "config.json"
        legacy = tmp_path / ".wallpace.json"
        legacy.write_text(
            '{"image_directories": ["D:/pics"], "switch_mode": "manual"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "LEGACY_CONFIG_PATHS", [legacy])

        s = Settings.__new__(Settings)
        s._using_default = True
        s.config_path = target
        s._data = {}
        assert s._try_migrate() is True
        assert s.get("image_directories") == ["D:/pics"]
        assert s.get("switch_mode") == "manual"
