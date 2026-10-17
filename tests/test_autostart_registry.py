"""tests/test_autostart_registry.py — 开机自启注册表管理测试。"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


class TestAutostartManagerImport:
    """测试模块可正常导入。"""

    def test_import_module(self) -> None:
        from src.core.autostart_registry import AutostartManager

        assert AutostartManager is not None

    def test_get_exe_path_returns_string(self) -> None:
        from src.core.autostart_registry import _get_exe_path

        path = _get_exe_path()
        assert isinstance(path, str)
        assert len(path) > 0


@pytest.fixture
def mocked_manager():
    """构造使用 fake winreg 的 AutostartManager，避免触碰真实注册表。"""
    fake_winreg = MagicMock()
    fake_key = MagicMock()
    fake_winreg.HKEY_CURRENT_USER = MagicMock()
    fake_winreg.OpenKey.return_value = fake_key
    fake_winreg.REG_SZ = 1
    with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
        from src.core.autostart_registry import AutostartManager

        yield AutostartManager()


class TestAutostartManagerPropertyEnabled:
    def test_is_enabled_no_crash(self, mocked_manager) -> None:
        result = mocked_manager.is_enabled
        assert isinstance(result, bool)


class TestAutostartManagerEnableDisable:
    def test_enable_no_crash(self, mocked_manager) -> None:
        result = mocked_manager.enable()
        assert isinstance(result, bool)

    def test_disable_no_crash(self, mocked_manager) -> None:
        result = mocked_manager.disable()
        assert isinstance(result, bool)

    def test_idempotent_enable(self, mocked_manager) -> None:
        mocked_manager.enable()
        mocked_manager.enable()


class TestAutostartManagerMockWinreg:
    """使用 mock winreg 验证核心逻辑。"""

    def test_is_enabled_true_when_exists(self) -> None:
        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = MagicMock()
        fake_winreg.OpenKey.return_value = fake_key
        fake_winreg.QueryValueEx.return_value = ("some_path", None)

        with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
            from src.core.autostart_registry import AutostartManager

            mgr = AutostartManager()
            assert mgr.is_enabled is True

    def test_is_enabled_false_when_not_found(self) -> None:
        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = MagicMock()
        fake_winreg.OpenKey.return_value = fake_key
        fake_winreg.QueryValueEx.side_effect = FileNotFoundError()

        with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
            from src.core.autostart_registry import AutostartManager

            mgr = AutostartManager()
            assert mgr.is_enabled is False

    def test_enable_writes_reg_value(self) -> None:
        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = MagicMock()
        fake_winreg.OpenKey.return_value = fake_key
        fake_winreg.REG_SZ = 1

        with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
            from src.core.autostart_registry import AutostartManager

            mgr = AutostartManager()
            result = mgr.enable()
            assert result is True
            fake_winreg.SetValueEx.assert_called_once()
            # call_args[0] = positional args: (hkey, lpValueName, Reserved, type, data)
            call_args = fake_winreg.SetValueEx.call_args[0]
            assert call_args[1] == "Wallpace"

    def test_disable_deletes_reg_value(self) -> None:
        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = MagicMock()
        fake_winreg.OpenKey.return_value = fake_key

        with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
            from src.core.autostart_registry import AutostartManager

            mgr = AutostartManager()
            result = mgr.disable()
            assert result is True
            fake_winreg.DeleteValue.assert_called_once()


class TestAutostartRegistryG02:
    """G02：启动时校验自启项指向的 exe 是否仍存在，坏项可被清理。"""

    def _make(self, query_return):
        """按给定的注册表值构造 manager；值格式与 enable() 写入一致。"""
        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = MagicMock()
        fake_winreg.OpenKey.return_value = fake_key
        fake_winreg.REG_SZ = 1
        fake_winreg.QueryValueEx.return_value = query_return
        fake_winreg.DeleteValue.return_value = None

        with patch.dict("sys.modules", {"winreg": fake_winreg}, create=True):
            from src.core.autostart_registry import AutostartManager

            return AutostartManager()

    def test_is_valid_true_when_exe_exists(self, tmp_path: Path) -> None:
        exe = tmp_path / "wallpace.exe"
        exe.write_bytes(b"MZ")
        mgr = self._make((f'"{exe}" --hidden', None))
        assert mgr.is_enabled is True
        assert mgr.is_valid() is True
        assert mgr.cleanup_invalid() is False  # 有效项不动

    def test_is_valid_false_when_exe_missing(self, tmp_path: Path) -> None:
        gone = tmp_path / "nope" / "wallpace.exe"
        mgr = self._make((f'"{gone}" --hidden', None))
        assert mgr.is_valid() is False
        assert mgr.cleanup_invalid() is True
        # 清理后注册表值应被删除
        mgr._winreg.DeleteValue.assert_called_once()

    def test_cleanup_invalid_with_empty_value_is_cleaned(self, tmp_path: Path) -> None:
        mgr = self._make(("", None))
        assert mgr.is_enabled is True  # 空串仍是"有值"（QueryValueEx 成功）
        assert mgr.is_valid() is False
        assert mgr.cleanup_invalid() is True
