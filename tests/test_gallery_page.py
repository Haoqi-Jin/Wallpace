"""tests/test_gallery_page.py — GalleryPage 分页懒加载与去重（P0-3 回归）。

修复前 refresh() 会为**全部**图片同步创建 _Tile（每个约 6 个 QObject），
400 张实测 1094 ms、800 张 7.8 s，且 showEvent 每次切页都完整重建。
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    """模块级 QApplication，避免每个用例反复创建/销毁。"""
    if not QApplication.instance():
        QApplication([])
    yield QApplication.instance()


def _make_library(paths):
    """构造一个 ImageLibrary，直接注入扫描结果（不走真实磁盘 IO）。"""
    from src.core.image_library import ImageLibrary

    lib = ImageLibrary(directories=[], extensions=set())
    lib._all_images = list(paths)
    return lib


@pytest.fixture(autouse=True)
def _quiet_image_loader():
    """用例里的路径是假文件，屏蔽解码失败的告警日志噪音。"""
    import logging

    logging.getLogger("src.app.image_loader").setLevel(logging.ERROR)
    yield


@pytest.fixture
def gallery(app, tmp_path):
    from src.app.pages.gallery_page import GalleryPage

    paths = [str(tmp_path / f"img_{i}.jpg") for i in range(120)]
    lib = _make_library(paths)
    page = GalleryPage(
        library=lib,
        image_loader=None,
        on_set_wallpaper=lambda p: None,
        on_persist=lambda: None,
    )
    yield page, lib, paths
    page.deleteLater()


class TestGalleryPagePagination:
    """refresh() 只创建首批，滚动时追加。"""

    def test_first_screen_only_creates_one_batch(self, gallery):
        page, _lib, paths = gallery
        assert len(paths) == 120
        assert page.rendered_count == 50
        assert page.pending_count == 70

    def test_grid_only_holds_rendered_tiles(self, gallery):
        page, _lib, _paths = gallery
        # 网格中实际的 widget 数量 == 已渲染数量（不是 120）
        assert page._grid.count() == page.rendered_count

    def test_append_batch_increments(self, gallery):
        page, _lib, _paths = gallery
        page._append_batch()
        assert page.rendered_count == 100
        assert page.pending_count == 20

    def test_load_all_renders_everything(self, gallery):
        page, _lib, paths = gallery
        page.load_all()
        assert page.rendered_count == len(paths)
        assert page.pending_count == 0

    def test_maybe_append_batch_is_noop_when_nothing_pending(self, gallery):
        page, _lib, _paths = gallery
        page.load_all()
        before = page.rendered_count
        page._maybe_append_batch()
        assert page.rendered_count == before


class TestGalleryPageRefreshDeduplication:
    """数据未变更时 refresh()/showEvent 不应重建 tile。"""

    def test_second_refresh_keeps_same_widgets(self, gallery):
        page, _lib, _paths = gallery
        first_widget = page._grid.itemAt(0).widget()
        page.refresh()
        assert page._grid.itemAt(0).widget() is first_widget

    def test_refresh_after_data_change_rebuilds(self, gallery):
        page, lib, paths = gallery
        first_widget = page._grid.itemAt(0).widget()
        lib._all_images = list(reversed(paths))
        page.refresh()
        assert page._grid.itemAt(0).widget() is not first_widget

    def test_refresh_after_favorite_change_rebuilds(self, gallery):
        """「全部」筛选下收藏状态变化：路径不变但 ★ 需要重绘。"""
        page, lib, paths = gallery
        first_widget = page._grid.itemAt(0).widget()
        lib.favorite(paths[0])
        page.refresh()
        assert page._grid.itemAt(0).widget() is not first_widget

    def test_force_refresh_rebuilds(self, gallery):
        page, _lib, _paths = gallery
        first_widget = page._grid.itemAt(0).widget()
        page.refresh(force=True)
        assert page._grid.itemAt(0).widget() is not first_widget

    def test_filter_switch_rebuilds(self, gallery):
        page, lib, paths = gallery
        lib.favorite(paths[0])
        page._set_filter("favorites")
        assert page.rendered_count == 1
        assert page.pending_count == 0


def _min_refresh_ms(page, lib, paths, repeat: int = 3) -> float:
    """取多次 refresh 的最小耗时，降低机器瞬时负载带来的抖动。"""
    best = float("inf")
    for i in range(repeat):
        lib._all_images = list(paths) if i % 2 == 0 else list(reversed(paths))
        start = time.perf_counter()
        page.refresh(force=True)
        best = min(best, (time.perf_counter() - start) * 1000)
    return best


def _make_page(tmp_path, count, tag):
    from src.app.pages.gallery_page import GalleryPage

    paths = [str(tmp_path / f"{tag}_{i}.jpg") for i in range(count)]
    lib = _make_library(paths)
    page = GalleryPage(
        library=lib,
        image_loader=None,
        on_set_wallpaper=lambda p: None,
        on_persist=lambda: None,
    )
    return page, lib, paths


class TestGalleryPagePerformance:
    """耗时与图库规模解耦（修复前随 N 线性增长）。

    用"相对比值"而非绝对毫秒做断言：绝对耗时受机器负载影响太大，
    而"成本是否随 N 增长"才是本次修复要保证的性质。
    """

    def test_refresh_400_creates_only_first_batch(self, tmp_path):
        from src.app.pages.gallery_page import THUMB_BATCH

        page, _lib, _paths = _make_page(tmp_path, 400, "cnt")
        try:
            assert page.rendered_count == THUMB_BATCH
            assert page._grid.count() == THUMB_BATCH
        finally:
            page.deleteLater()

    def test_refresh_cost_is_independent_of_library_size(self, tmp_path):
        """800 张与 100 张的单次 refresh 耗时应在同一量级（修复前相差 8x+）。

        实测（隔离进程）：修复前 100→150 ms / 800→1293 ms（8.6x）；
        修复后恒为 ~70-90 ms（1.0x）。阈值取 3x 留足余量。
        """
        small_page, small_lib, small_paths = _make_page(tmp_path, 100, "small")
        large_page, large_lib, large_paths = _make_page(tmp_path, 800, "large")
        try:
            t_small = _min_refresh_ms(small_page, small_lib, small_paths)
            t_large = _min_refresh_ms(large_page, large_lib, large_paths)
            ratio = t_large / max(t_small, 0.001)
            assert ratio < 3.0, (
                f"refresh 耗时随图库规模增长: 100 张 {t_small:.0f} ms, "
                f"800 张 {t_large:.0f} ms (比值 {ratio:.1f}x)"
            )
        finally:
            small_page.deleteLater()
            large_page.deleteLater()


class TestGalleryPageCleanup:
    """重建时必须退订旧 tile 的解码回调，避免野回调（P0-1 同源）。

    这里用 monkeypatch 替换 image_loader 的 load_async/cancel 来观察调用，
    避免受后台解码线程完成时序影响（真实解码是异步的，直接查订阅表会有竞态）。
    """

    def test_rebuild_cancels_old_subscriptions(self, gallery, monkeypatch):
        from src.app import image_loader

        page, lib, paths = gallery
        page.load_all()
        old_tiles = [
            page._grid.itemAt(i).widget() for i in range(page._grid.count())
        ]
        assert len(old_tiles) == 120

        cancelled = []
        monkeypatch.setattr(
            image_loader, "cancel", lambda p, cb: cancelled.append((p, cb))
        )

        lib._all_images = list(reversed(paths))
        page.refresh(force=True)

        for tile in old_tiles:
            assert (tile._path, tile._on_image_ready) in cancelled

    def test_rebuild_subscribes_new_tiles(self, gallery, monkeypatch):
        from src.app import image_loader

        page, lib, paths = gallery
        calls = []
        monkeypatch.setattr(
            image_loader,
            "load_async",
            lambda p, size, on_ready=None: calls.append((p, on_ready)),
        )

        lib._all_images = list(reversed(paths))
        page.refresh(force=True)

        assert len(calls) == page.rendered_count
        for i in range(page.rendered_count):
            tile = page._grid.itemAt(i).widget()
            assert (tile._path, tile._on_image_ready) in calls
