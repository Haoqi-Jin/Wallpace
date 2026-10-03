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


def _highlighted(strip):
    """返回当前处于高亮态的缩略图路径。

    高亮与否**只认 ``GalleryThumbWidget`` 自身由 ``set_is_current`` 写入的真实
    状态**（``_is_current``），而非某个聚合变量——避免"改了个字段却没真正改控件"
    的 no-op 修复蒙混过关。
    """
    from src.app.widgets.gallery_strip import GalleryThumbWidget

    return [w._image_path for w in strip.findChildren(GalleryThumbWidget) if w._is_current]


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


class TestGalleryStripHighlightDrift:
    """回归：set_current 必须回写 _current_path，否则懒加载新批次高亮漂移。

    缺陷（QA 审计结论 1）：``set_current()`` 只改现有 widget、不更新
    ``self._current_path``；``_append_batch()`` 却按 ``self._current_path`` 判定
    新批次高亮 → 切换后滚动懒加载，同屏出现两个高亮项。
    """

    def test_no_stale_highlight_in_lazy_batch_after_switch(self, strip):
        """直接症状：切换到 p1 后追加的新批次不得把陈旧 p0 点亮。"""
        from src.app.widgets.gallery_strip import GalleryStrip

        s, _ = strip
        batch = GalleryStrip.THUMB_BATCH
        p1 = "/img/current.jpg"
        p0 = "/img/stale.jpg"  # 故意放在第二批（懒加载），首批不含它
        others = [f"/img/other{i}.jpg" for i in range(1, batch)]
        paths = [p1, *others, p0]  # 共 batch+1 个，p0 是第二批第一个

        s.reload(paths, current_path=p0)
        assert _highlighted(s) == []  # 首批不含 p0，故无高亮

        s.set_current(p1)
        assert _highlighted(s) == [p1]

        s._append_batch()  # 触发懒加载，p0 此时才建控件

        hl = _highlighted(s)
        assert hl == [p1], f"追加批次后高亮应仍只有 p1，实际: {hl}"
        assert p0 not in hl, "陈旧 _current_path 把 p0 也点亮了（高亮漂移）"

    def test_highlight_is_never_more_than_one(self, strip):
        """任意状态下同屏高亮项数恒 ≤ 1（高亮互斥不变量）。"""
        from src.app.widgets.gallery_strip import GalleryStrip

        s, _ = strip
        batch = GalleryStrip.THUMB_BATCH
        paths = [f"/img/{i}.jpg" for i in range(batch * 3)]

        s.reload(paths, current_path=paths[0])
        s.set_current(paths[batch + 2])  # 切到尚未加载的第二批
        assert len(_highlighted(s)) <= 1

        s._append_batch()
        s._append_batch()
        assert len(_highlighted(s)) <= 1

    def test_set_current_drives_real_widget_state_not_just_field(self, strip):
        """反 no-op：set_current 经 set_is_current 真正改控件状态（含样式）。"""
        s, _ = strip
        s.reload(["/img/a.jpg", "/img/b.jpg"], current_path="/img/a.jpg")
        a = _widget_at(s, 0)
        b = _widget_at(s, 1)

        assert a._is_current is True and b._is_current is False

        s.set_current("/img/b.jpg")

        assert a._is_current is False and b._is_current is True
        # 高亮通过真实样式落盘（_apply_style），不只是内存里的布尔
        assert "#ec4899" in b.styleSheet()
        assert "#ec4899" not in a.styleSheet()
