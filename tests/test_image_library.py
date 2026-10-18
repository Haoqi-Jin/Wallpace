"""tests/test_image_library.py -- ImageLibrary module tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.image_library import DEFAULT_EXTENSIONS, ImageLibrary


class TestImageLibraryScan:
    def test_scan_empty_directories(self):
        lib = ImageLibrary(directories=[])
        images = lib.scan()
        assert images == []
        assert lib.total_count == 0

    def test_scan_invalid_directory(self, sample_image_files):
        lib = ImageLibrary(directories=["/nonexistent/path"])
        images = lib.scan()
        assert images == []

    def test_scan_valid_images(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        images = lib.scan()
        # 默认扩展名含 jpg/png/webp/bmp/gif：
        # a.jpg, b.png, c.webp, d.bmp, sub/e.jpg
        assert len(images) == 5
        for img in images:
            ext = Path(img).suffix.lower().lstrip(".")
            assert ext in DEFAULT_EXTENSIONS

    def test_scan_excludes_txt(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        images = lib.scan()
        names = [Path(i).name for i in images]
        assert "readme.txt" not in names

    def test_scan_respects_extensions(self, sample_image_files):
        lib = ImageLibrary(
            directories=[sample_image_files],
            extensions={"jpg"},
        )
        images = lib.scan()
        assert all(i.endswith(".jpg") for i in images)

    def test_scan_returns_absolute_paths(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        images = lib.scan()
        for img in images:
            assert Path(img).is_absolute()

    def test_scan_nested_directories(self, tmp_path):
        nested = tmp_path / "nested"
        nested.mkdir(parents=True)
        nested_file = nested / "nested.jpg"
        nested_file.write_text("dummy image content")

        lib = ImageLibrary(directories=[str(tmp_path)])
        images = lib.scan()

        assert str(nested_file.resolve()) in images
        assert images.count(str(nested_file.resolve())) == 1


class TestImageLibraryRandom:
    def test_get_random_empty(self):
        lib = ImageLibrary(directories=["/nonexistent"])
        result = lib.get_random()
        assert result is None

    def test_get_random_returns_valid_path(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        path = lib.get_random()
        assert path is not None
        assert Path(path).exists()

    def test_get_random_from_available_only(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        for img in list(lib.list_available()):
            lib.skip(img)
        result = lib.get_random()
        assert result is None

    # --- G08: get_random_candidates ---

    def test_candidates_empty_when_no_images(self):
        lib = ImageLibrary(directories=["/nonexistent"])
        assert lib.get_random_candidates() == []

    def test_candidates_distinct_and_within_available(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        total = len(lib.list_available())
        candidates = lib.get_random_candidates(count=3)
        assert len(candidates) == min(3, total)
        # 互不相同（sample 保证）
        assert len(set(candidates)) == len(candidates)
        for c in candidates:
            assert c in lib.list_available()

    def test_candidates_respect_skip(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        first = lib.list_available()[0]
        lib.skip(first)
        for _ in range(20):  # 多试几次防随机巧合
            assert first not in lib.get_random_candidates(count=3)


class TestImageLibrarySkipFavorite:
    def test_skip_marks_image(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        first = lib.list_available()[0]
        added = lib.skip(first)
        assert added is True
        available = lib.list_available()
        assert first not in available
        assert first in lib.skip_list

    def test_skip_already_skipped(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        first = lib.list_available()[0]
        lib.skip(first)
        assert lib.skip(first) is False

    def test_unskip_restores_image(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        first = lib.list_available()[0]
        lib.skip(first)
        removed = lib.unskip(first)
        assert removed is True
        assert first in lib.list_available()

    def test_favorite_and_unfavorite(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        first = lib.list_available()[0]
        assert lib.favorite(first) is True
        assert first in lib.favorites
        assert lib.unfavorite(first) is True
        assert first not in lib.favorites

    def test_clear_skip(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        for img in list(lib.list_available()):
            lib.skip(img)
        count = lib.clear_skip()
        assert count > 0
        assert lib.total_count > 0
        assert len(lib.list_available()) == lib.total_count

    def test_clear_favorites(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        avail = lib.list_available()[:2]
        for img in avail:
            lib.favorite(img)
        count = lib.clear_favorites()
        assert count > 0
        assert len(lib.favorites) == 0


class TestImageLibraryDirectories:
    def test_add_directory(self, sample_image_files):
        lib = ImageLibrary()
        assert lib.add_directory(sample_image_files) is True
        assert lib.directory_count == 1

    def test_add_invalid_directory(self):
        lib = ImageLibrary()
        assert lib.add_directory("/nonexistent/path") is False

    def test_remove_directory(self, sample_image_files):
        lib = ImageLibrary(directories=[sample_image_files])
        lib.scan()
        assert lib.remove_directory(sample_image_files) is True
        assert lib.directory_count == 0
        assert lib.total_count == 0


class TestImageLibrarySerialize:
    def test_to_dict_round_trip(self, sample_image_files):
        lib1 = ImageLibrary(directories=[sample_image_files])
        lib1.scan()
        data = lib1.to_dict()
        lib2 = ImageLibrary.from_dict(data)
        assert lib2._directories == lib1._directories
        assert lib2._extensions == lib1._extensions


class TestDefaultExtensions:
    """默认扩展名需与 README 功能表一致（含 BMP/GIF）。"""

    def test_bmp_and_gif_supported_by_default(self):
        assert {"bmp", "gif"} <= DEFAULT_EXTENSIONS

    def test_scan_finds_bmp_and_gif(self, tmp_path):
        d = tmp_path / "imgs"
        d.mkdir()
        for name in ("x.bmp", "y.gif", "z.jpg"):
            (d / name).write_bytes(b"x")
        images = ImageLibrary(directories=[str(d)]).scan()
        assert {Path(i).suffix.lower() for i in images} == {".bmp", ".gif", ".jpg"}

    def test_explicit_extensions_still_override_default(self, tmp_path):
        d = tmp_path / "imgs"
        d.mkdir()
        for name in ("x.bmp", "y.jpg"):
            (d / name).write_bytes(b"x")
        images = ImageLibrary(
            directories=[str(d)], extensions={"jpg"}
        ).scan()
        assert {Path(i).suffix.lower() for i in images} == {".jpg"}


class TestIncrementalScan:
    """目录未变化时复用缓存；一旦变化必须能被感知（宁可多扫、不可漏扫）。"""

    @staticmethod
    def _lib(tmp_path):
        d = tmp_path / "imgs"
        d.mkdir()
        (d / "a.jpg").write_bytes(b"x")
        return ImageLibrary(directories=[str(d)]), d

    def test_reuses_cache_when_unchanged(self, tmp_path):
        lib, _ = self._lib(tmp_path)
        first = lib.scan()
        sig = lib._last_scan_signature
        assert sig is not None
        assert lib.scan() == first
        assert lib._last_scan_signature == sig

    def test_detects_added_file(self, tmp_path):
        lib, d = self._lib(tmp_path)
        assert len(lib.scan()) == 1
        (d / "b.png").write_bytes(b"x")
        assert len(lib.scan()) == 2

    def test_detects_removed_file(self, tmp_path):
        lib, d = self._lib(tmp_path)
        (d / "b.png").write_bytes(b"x")
        assert len(lib.scan()) == 2
        (d / "b.png").unlink()
        assert len(lib.scan()) == 1

    def test_detects_change_in_subdirectory(self, tmp_path):
        lib, d = self._lib(tmp_path)
        sub = d / "sub"
        sub.mkdir()
        assert len(lib.scan()) == 1
        (sub / "deep.png").write_bytes(b"x")
        assert len(lib.scan()) == 2

    def test_detects_new_subdir_file_same_tick(self, tmp_path):
        """回归：同一 tick 内「mkdir + 立即写入子文件」也必须被感知。

        Windows 的目录 mtime 按系统时钟 tick（约 15.6ms）量化：若增量签名只
        依赖 mtime，紧接 mkdir 的写入会落在同一 tick 内 → 父/子目录 mtime 均
        不变 → 签名与上次相同 → 误判"未变化"而漏扫。本用例不 sleep、不在
        mkdir 与写入之间插入 scan，专门复现这个时序，断言必须感知到变化。
        """
        lib, d = self._lib(tmp_path)
        assert len(lib.scan()) == 1
        sig_before = lib._last_scan_signature
        sub = d / "sub"
        sub.mkdir()
        (sub / "deep.png").write_bytes(b"x")  # 紧接 mkdir，无中间 scan / sleep
        # 签名必须以「条目数」为主判据而变化，否则会命中缓存漏扫。
        assert lib._directory_signature() != sig_before
        assert len(lib.scan(force=False)) == 2

    def test_detects_added_file_same_tick(self, tmp_path):
        """回归：父目录内同一 tick 内立即写入新文件也必须被感知。"""
        lib, d = self._lib(tmp_path)
        assert len(lib.scan()) == 1
        sig_before = lib._last_scan_signature
        (d / "b.png").write_bytes(b"x")  # 紧接上次 scan
        assert lib._directory_signature() != sig_before
        assert len(lib.scan(force=False)) == 2

    def test_force_rescans(self, tmp_path):
        lib, d = self._lib(tmp_path)
        lib.scan()
        (d / "b.png").write_bytes(b"x")
        assert len(lib.scan(force=True)) == 2

    def test_missing_directory_disables_incremental(self, tmp_path):
        """目录不可用时签名返回 None，退化为全量扫描（不缓存错误结果）。"""
        lib = ImageLibrary(directories=[str(tmp_path / "nope")])
        assert lib.scan() == []
        assert lib._last_scan_signature is None
