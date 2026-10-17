"""tests/test_diagnostics.py — 诊断入口（G07）测试。

验证「日志目录单一真源 + 跨平台打开动作」的核心行为：
成功打开返回 True、失败不抛异常而返回 False、目录缺失时先创建。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core import diagnostics


def test_open_logs_creates_missing_dir_and_returns_true(tmp_path: Path) -> None:
    """目录不存在时先创建，再用默认程序打开（Windows 分支 mock 掉 startfile）。"""
    fake_logs = tmp_path / "logs"
    with (
        patch.object(diagnostics, "LOGS_DIR", fake_logs),
        patch.object(diagnostics.os, "name", new="nt"),
        patch.object(diagnostics.os, "startfile", create=True) as mock_start,
    ):
        assert diagnostics.open_logs_directory() is True
    assert fake_logs.exists()
    mock_start.assert_called_once_with(fake_logs)


def test_open_logs_failure_returns_false_not_raises(tmp_path: Path) -> None:
    """打开失败（如 startfile 抛 OSError）不得向上抛，返回 False。"""
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    with (
        patch.object(diagnostics, "LOGS_DIR", fake_logs),
        patch.object(
            diagnostics.os, "startfile", side_effect=OSError("no explorer"),
            create=True,
        ),
    ):
        assert diagnostics.open_logs_directory() is False


def test_open_logs_non_nt_falls_back_to_xdg_open(tmp_path: Path) -> None:
    """非 Windows：优先 xdg-open，找不到就 open，再找不到返回 False。"""
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    with (
        patch.object(diagnostics, "LOGS_DIR", fake_logs),
        patch.object(diagnostics.os, "name", new="posix"),
        patch.object(diagnostics.shutil, "which", return_value="/usr/bin/xdg-open"),
        patch.object(diagnostics.subprocess, "Popen") as mock_popen,
    ):
        assert diagnostics.open_logs_directory() is True
    mock_popen.assert_called_once_with(["/usr/bin/xdg-open", str(fake_logs)])


def test_open_logs_no_opener_returns_false(tmp_path: Path) -> None:
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    with (
        patch.object(diagnostics, "LOGS_DIR", fake_logs),
        patch.object(diagnostics.os, "name", new="posix"),
        patch.object(diagnostics.shutil, "which", return_value=None),
    ):
        assert diagnostics.open_logs_directory() is False
