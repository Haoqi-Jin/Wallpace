"""tests/test_preview_card.py — PreviewCardWidget 测试。

测试预览卡片的 UI 行为和信号。
"""

import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QImageWriter
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.app.widgets.preview_card import PreviewCardWidget


@pytest.fixture
def app():
    """确保有 QApplication 实例。"""
    if not QApplication.instance():
        QApplication([])
    yield


@pytest.fixture
def preview_card(app):
    """创建预览卡片实例。"""
    return PreviewCardWidget()


class TestPreviewCardInit:
    """测试初始化。"""

    def test_creates_widget(self, preview_card):
        assert preview_card is not None
        assert isinstance(preview_card, PreviewCardWidget)

    def test_default_path_empty(self, preview_card):
        assert preview_card._current_path == ""

    def test_default_index_zero(self, preview_card):
        assert preview_card._current_index == 0

    def test_default_total_zero(self, preview_card):
        assert preview_card._total_count == 0


class TestPreviewCardSignals:
    """测试信号。"""

    def test_switch_clicked_signal(self, preview_card):
        """测试换一张信号存在。"""
        assert hasattr(preview_card, "switch_clicked")

    def test_skip_clicked_signal(self, preview_card):
        """测试跳过信号存在。"""
        assert hasattr(preview_card, "skip_clicked")

    def test_favorite_clicked_signal(self, preview_card):
        """测试收藏信号存在。"""
        assert hasattr(preview_card, "favorite_clicked")


class TestPreviewCardUpdate:
    """测试更新方法。"""

    def test_update_preview_sets_path(self, preview_card, tmp_path):
        """测试 update_preview 设置路径。"""
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(path), "JPG")

        preview_card.update_preview(str(path), 0, 10)
        assert preview_card._current_path == str(path)
        assert preview_card._current_index == 0
        assert preview_card._total_count == 10

    def test_update_preview_updates_filename(self, preview_card, tmp_path):
        """测试更新文件名显示。"""
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        path = tmp_path / "my_wallpaper.jpg"
        QImageWriter.write(img, str(path), "JPG")

        preview_card.update_preview(str(path), 2, 10)
        # 文件名应该显示
        assert "my_wallpaper" in preview_card.filename_label.text()

    def test_clear_preview_resets_state(self, preview_card, tmp_path):
        """测试 clear_preview 重置状态。"""
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(path), "JPG")

        preview_card.update_preview(str(path), 0, 10)
        preview_card.clear_preview()

        assert preview_card._current_path == ""
        assert preview_card._current_index == 0
        assert preview_card._total_count == 0

    def test_clear_preview_clears_labels(self, preview_card, tmp_path):
        """测试 clear_preview 清空标签。"""
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0xFF0000)
        path = tmp_path / "test.jpg"
        QImageWriter.write(img, str(path), "JPG")

        preview_card.update_preview(str(path), 0, 10)
        preview_card.clear_preview()

        assert preview_card.filename_label.text() == "未选图片"
        assert preview_card.path_label.text() == ""
        assert preview_card.index_label.text() == ""
