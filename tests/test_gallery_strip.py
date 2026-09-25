"""tests/test_gallery_strip.py — GalleryStrip 独立单测（#1 搬迁）。

脱离 MainWindow 验证：
- reload() 首批懒加载语义（只创建 THUMB_BATCH 个控件）；
- 重复 reload() 幂等（widget 数稳定，保持 P0-1 修复）；
- set_current() 高亮互斥；
- clicked → on_select 回调；
- _append_batch() 推进懒加载状态机。
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not QApplication.instance():
        QApplication([])
    yield


@pytest.fixture
def strip(app, monkeypatch):
    """构造被测 GalleryStrip；把异步解码替换为 no-op 保证测试确定、快速。"""
    from src.app.widgets import gallery_strip

    monkeypatch.setattr(
        gallery_strip.image_loader, "load_async", lambda *a, **k: None
    )
    monkeypatch.setattr(gallery_strip.image_loader, "cancel", lambda *a, **k: None)

    selected: list = []
    s = gallery_strip.GalleryStrip(on_select=selected.append)
    yield s, selected


def _widget_at(strip, index):
    item = strip.layout().itemAt(index)
    return item.widget() if item is not None else None


class TestGalleryStripReload:
    """reload() 的懒加载与幂等语义。"""

    def test_first_batch_only(self, strip):
        from src.app.widgets.gallery_strip import GalleryStrip, GalleryThumbWidget

        s, _ = strip
        paths = [f"/img/{i}.jpg" for i in range(25)]
        s.reload(paths, current_path="/img/0.jpg")

        assert s.layout().count() == GalleryStrip.THUMB_BATCH
        # 真实子控件数与布局项数一致（验证确实 addWidget 了）
        assert len(s.findChildren(GalleryThumbWidget)) == GalleryStrip.THUMB_BATCH

    def test_reload_is_idempotent(self, strip):
        s, _ = strip
        paths = [f"/img/{i}.jpg" for i in range(8)]
        s.reload(paths, "")
        first = s.layout().count()

        s.reload(paths, "")  # 二次重建不得累加/抛异常（P0-1）

        assert s.layout().count() == first
        assert first == 8

    def test_empty_paths(self, strip):
        s, _ = strip
        s.reload([], "")
        assert s.layout().count() == 0

    def test_append_batch_advances_pending(self, strip):
        from src.app.widgets.gallery_strip import GalleryStrip

        s, _ = strip
        paths = [f"/img/{i}.jpg" for i in range(25)]
        s.reload(paths, "")
        assert len(s._pending) == 25 - GalleryStrip.THUMB_BATCH

        s._append_batch()
        assert s.layout().count() == 2 * GalleryStrip.THUMB_BATCH


class TestGalleryStripHighlight:
    """set_current() 高亮互斥。"""

    def test_current_highlight_is_exclusive(self, strip):
        s, _ = strip
        s.reload(["/img/a.jpg", "/img/b.jpg", "/img/c.jpg"], current_path="/img/b.jpg")

        assert _widget_at(s, 0)._is_current is False
        assert _widget_at(s, 1)._is_current is True
        assert _widget_at(s, 2)._is_current is False

        s.set_current("/img/a.jpg")
        assert _widget_at(s, 0)._is_current is True
        assert _widget_at(s, 1)._is_current is False


class TestGalleryStripSelection:
    """点击缩略图 → on_select 回调。"""

    def test_click_invokes_on_select(self, strip):
        s, selected = strip
        s.reload(["/img/a.jpg"], "")

        widget = _widget_at(s, 0)
        widget.clicked.emit(widget._image_path)

        assert selected == ["/img/a.jpg"]
