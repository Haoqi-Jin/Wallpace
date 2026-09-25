"""src/core/image_library.py — 图片库管理模块。

负责扫描用户配置的图片文件夹、过滤有效图片、管理跳过列表和收藏夹。
不依赖任何 GUI 框架，纯 Python 标准库 + pathlib。
"""

import hashlib
import logging
import os
import random
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 默认扩展名集合（小写）
# 与 README 功能表保持一致（含 BMP/GIF）。GIF 动图由 QImageReader 读取首帧，
# 作为缩略图/壁纸均可接受。
DEFAULT_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}


def _iter_image_files(root: Path, supported: Set[str]) -> Iterator[Path]:
    """递归产出 root 下扩展名受支持的图片文件（已 resolve）。

    这里用 `os.scandir` 手工递归，而不是 `root.rglob("*")`：
    rglob 会为目录下**每一个**文件构造 Path 对象并 stat，再交给调用方用
    suffix 过滤；一个包含 10 万个无关文件的目录就白付 10 万次开销。
    scandir 的 DirEntry 在 Windows 上自带类型信息，可省掉额外 stat，
    且我们在遍历时就丢弃无关条目，只对命中的图片做 resolve。
    """
    stack: List[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            name = entry.name
                            dot = name.rfind(".")
                            # dot > 0 同时排除了无扩展名文件与 `.hidden` 这类
                            # 以点开头的文件（与旧实现 suffix 为空的行为一致）
                            if dot > 0 and name[dot + 1:].lower() in supported:
                                yield Path(entry.path).resolve()
                    except OSError:
                        # 单个条目不可读不应中断整个扫描
                        continue
        except OSError:
            logger.warning("无法读取目录: %s", current)


class ImageLibrary:
    """壁纸图片库管理器。

    提供目录递归扫描、扩展名过滤、随机选取、跳过/收藏黑名单等功能。
    所有路径统一使用绝对路径（str），方便序列化到 JSON。
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        extensions: Optional[Set[str]] = None,
    ) -> None:
        """初始化图片库。

        Args:
            directories: 要扫描的图片文件夹列表；空列表则跳过扫描。
            extensions: 允许的扩展名集合（不含点，全部小写）。
        """
        self._directories: List[str] = directories or []
        self._extensions = (
            {e.lower().lstrip(chr(46)) for e in extensions}
            if extensions
            else set(DEFAULT_EXTENSIONS)
        )
        # 运行时状态
        self._all_images: List[str] = []  # scan() 的结果缓存
        # 上次扫描时的目录 mtime 签名，用于增量扫描（None = 尚无有效签名）
        self._last_scan_signature: Optional[Tuple[str, ...]] = None
        self._skip_set: Set[str] = set()   # 去重用
        self._favorite_set: Set[str] = set()

    # ==================== 公开 API ====================

    def scan(self, force: bool = False) -> List[str]:
        """递归扫描所有配置的文件夹，返回可用图片路径。

        带增量检查：若所有目录（含子目录）的签名（目录条目数 + mtime）与上次
        一致，说明期间没有文件增删，直接复用上次结果，省掉一次全量遍历。
        注意签名以「条目数」为主判据 —— 仅凭 mtime 会因 Windows 时间戳量化
        而漏掉同一 tick 内的增删（详见 ``_directory_signature`` 的说明）。

        Args:
            force: True 时跳过增量检查，强制全量重扫。

        Returns:
            符合条件的图片绝对路径列表（已去重、排序）。
        """
        signature = self._directory_signature()
        if (
            not force
            and signature is not None
            and signature == self._last_scan_signature
        ):
            logger.debug("目录未变化，复用上次扫描结果: %d 张", len(self._all_images))
            return list(self._all_images)

        images: List[str] = []
        for dir_str in self._directories:
            root = Path(dir_str)
            if not root.is_dir():
                logger.warning("跳过无效目录: %s", dir_str)
                continue
            images.extend(str(p) for p in _iter_image_files(root, self._extensions))

        # 去重 + 排序
        unique: List[str] = []
        seen: Set[str] = set()
        for img in sorted(images):
            if img not in seen:
                seen.add(img)
                unique.append(img)
        self._all_images = unique
        self._last_scan_signature = signature
        logger.info("扫描完成: %d 张图片", len(unique))
        return unique

    def refresh(self, force: bool = False) -> List[str]:
        """快捷调用 scan + reload 的组合。

        Args:
            force: True 时强制全量重扫（跳过增量检查）。
        """
        return self.scan(force=force)

    def _directory_signature(self) -> Optional[Tuple[str, ...]]:
        """采集所有配置目录（含子目录）的状态签名，用于增量扫描判断。

        每个目录同时记录两项信息（拼成 ``路径|mtime_ns|条目数``）：

        1. **条目数** —— 无条件可靠。目录内任何文件/子目录的增删都会立即改变
           该目录的条目数，与文件系统时间戳精度无关。这是检测增删的**主**判据。
        2. **mtime_ns** —— 用于捕捉「条目数不变但内容/名字变了」的边缘场景，
           例如删除 ``a.jpg`` 后在同一目录又新建同名文件、或把文件改名等。

        ⚠️ 为什么**不能只用 mtime**（历史坑，后人请勿"优化"回去）：
            Windows 的 NTFS 文件时间戳按系统时钟 tick 量化，粒度约 15.6ms。
            当「新建目录 → 立刻写入文件」发生在**同一个 tick 内**时，父目录与
            被新建子目录的 mtime 完全不变，签名与上次相同，增量逻辑会误判
            「目录未变化」而复用旧缓存，导致用户新加的图片不显示（漏扫）。
            实测（Python 3.13 / Windows）：同一 tick 内 mkdir+写入，目录 mtime
            Δ = 0 ns；延迟 5ms 后 Δ ≈ 7.3e6 ns，延迟 20ms 后 Δ ≈ 2.4e7 ns。
            因此必须叠加「条目数」这一免疫时间戳量化的判据。

        成本：本方法依旧**只对目录**做 scandir，并在同一次遍历中顺带累加条目数，
        零额外系统调用 —— 快速路径的成本相比"只记 mtime"的版本没有增加。

        Returns:
            排序后的签名元组；只要有任何配置目录缺失/不可读就返回 None，
            表示「无法安全增量」，调用方会退化为全量扫描（宁可多扫不漏扫）。
        """
        entries: List[str] = []
        for dir_str in self._directories:
            root = Path(dir_str)
            if not root.is_dir():
                return None
            stack = [root]
            while stack:
                current = stack.pop()
                try:
                    count = 0
                    with os.scandir(current) as it:
                        for entry in it:
                            count += 1
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    stack.append(Path(entry.path))
                            except OSError:
                                continue
                    # 条目数在遍历中免费得到，是检测增删的主判据；
                    # mtime 追加用于捕捉条目数不变的场景。
                    mtime_ns = current.stat().st_mtime_ns
                    entries.append(f"{current}|{mtime_ns}|{count}")
                except OSError:
                    return None
        return tuple(sorted(entries))

    def list_available(self) -> List[str]:
        """获取当前可用的图片列表（排除已跳过的）。

        Returns:
            未被标记为 skip 的图片路径。
        """
        return [img for img in self._all_images if img not in self._skip_set]

    def get_random(self) -> Optional[str]:
        """从可用列表中随机选择一张图片。

        Returns:
            图片路径或 None（无可用图片时）。
        """
        available = self.list_available()
        if not available:
            logger.warning("没有可用图片，请先添加图片目录")
            return None
        return random.choice(available)

    def add_directory(self, path: str, scan: bool = True) -> bool:
        """动态添加扫描目录。

        Args:
            path: 文件夹路径。
            scan: 是否立即重新扫描目录。

        Returns:
            True 表示目录有效且已添加。
        """
        p = Path(path)
        if not p.is_dir():
            logger.warning("无法添加无效目录: %s", path)
            return False
        if path not in self._directories:
            self._directories.append(path)
            logger.info("添加图片目录: %s", path)
        if scan:
            self.scan()
        return True

    def remove_directory(self, path: str, scan: bool = True) -> bool:
        """移除扫描目录。

        Args:
            path: 要移除的目录路径。
            scan: 是否立即重新扫描目录。

        Returns:
            True 表示成功移除。
        """
        if path in self._directories:
            self._directories.remove(path)
            if scan:
                self.scan()
            logger.info("移除图片目录: %s", path)
            return True
        return False

    # ==================== 黑白名单管理 ====================

    def skip(self, image_path: str) -> bool:
        """将图片加入跳过列表（不删除原文件，仅标记）。

        Args:
            image_path: 要跳过的图片路径。

        Returns:
            True 表示新增了跳过项；该路径已在跳过列表中则返回 False。
        """
        resolved = Path(image_path).resolve()
        key = str(resolved)
        if key in self._skip_set:
            return False
        self._skip_set.add(key)
        logger.info("跳过图片: %s", key)
        return True

    def unskip(self, image_path: str) -> bool:
        """从跳过列表中移除一张图片。

        Args:
            image_path: 要恢复的图片路径。

        Returns:
            True 表示成功移除；不在跳过列表中则返回 False。
        """
        resolved = Path(image_path).resolve()
        key = str(resolved)
        removed = key in self._skip_set
        self._skip_set.discard(key)
        if removed:
            logger.info("取消跳过: %s", key)
        return removed

    def favorite(self, image_path: str) -> bool:
        """将图片加入收藏列表。

        Args:
            image_path: 要收藏的图片路径。

        Returns:
            True 表示新增收藏。
        """
        resolved = Path(image_path).resolve()
        key = str(resolved)
        added = key not in self._favorite_set
        self._favorite_set.add(key)
        if added:
            logger.info("收藏: %s", key)
        return added

    def is_favorite(self, image_path: str) -> bool:
        """判断图片是否已经收藏。"""
        resolved = Path(image_path).resolve()
        return str(resolved) in self._favorite_set

    def unfavorite(self, image_path: str) -> bool:
        """取消收藏。

        Args:
            image_path: 图片路径。

        Returns:
            True 表示取消了收藏。
        """
        resolved = Path(image_path).resolve()
        key = str(resolved)
        removed = key in self._favorite_set
        self._favorite_set.discard(key)
        if removed:
            logger.info("取消收藏: %s", key)
        return removed

    def load_persisted_state(self, skip_list: List[str], favorites: List[str]) -> None:
        """从持久化配置载入跳过列表与收藏列表。

        在应用启动时调用，把 Settings 中保存的 skip_list / favorites 还原到内存集合。
        路径用 Path.resolve() 归一化，与 skip()/favorite() 的键保持一致。

        Args:
            skip_list: 跳过的图片绝对路径列表。
            favorites: 收藏的图片绝对路径列表。
        """
        self._skip_set = {str(Path(p).resolve()) for p in skip_list if p}
        self._favorite_set = {str(Path(p).resolve()) for p in favorites if p}
        logger.info(
            "已载入持久化状态: %d 跳过, %d 收藏",
            len(self._skip_set), len(self._favorite_set),
        )

    @property
    def skip_list(self) -> List[str]:
        """返回跳过列表的副本（可安全用于序列化）。"""
        return sorted(self._skip_set)

    @property
    def favorites(self) -> List[str]:
        """返回收藏列表的副本。"""
        return sorted(self._favorite_set)

    # ==================== 状态查询 ====================

    def clear_skip(self) -> int:
        """清空跳过列表，返回被清除的数量。"""
        count = len(self._skip_set)
        self._skip_set.clear()
        logger.info("清空跳过列表: %d 项", count)
        return count

    def clear_favorites(self) -> int:
        """清空收藏列表，返回被清除的数量。"""
        count = len(self._favorite_set)
        self._favorite_set.clear()
        logger.info("清空收藏列表: %d 项", count)
        return count

    @property
    def total_count(self) -> int:
        """已扫描的总图片数（含已被跳过的）。"""
        return len(self._all_images)

    @property
    def available_count(self) -> int:
        """当前可用的图片数（排除跳过）。"""
        return len(self.list_available())

    @property
    def directory_count(self) -> int:
        """已配置的扫描目录数量。"""
        return len(self._directories)

    def to_dict(self) -> dict:
        """导出为可序列化的字典。"""
        return {
            "directories": list(self._directories),
            "extensions": sorted(self._extensions),
            "skip_list": self.skip_list,
            "favorites": self.favorites,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageLibrary":
        """从字典重建实例。"""
        lib = cls(
            directories=data.get("directories"),
            extensions=data.get("extensions"),
        )
        lib._skip_set = set(data.get("skip_list", []))
        lib._favorite_set = set(data.get("favorites", []))
        return lib
