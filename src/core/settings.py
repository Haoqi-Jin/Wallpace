"""src/core/settings.py — 配置管理模块。

负责读取、保存、验证应用 JSON 配置文件。
遵循 dev-standards.md 编码规范：Python 3.10+, docstring, 类型注解。
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认配置常量
# 注意：extensions 必须与 image_library.DEFAULT_EXTENSIONS 保持一致，
# 否则新用户首次运行只会拿到旧的 4 种扩展名（Settings 的值会作为显式
# extensions 传进 ImageLibrary，覆盖后者的默认值）。
DEFAULT_CONFIG = {
    "image_directories": [],
    "extensions": ["jpg", "jpeg", "png", "webp", "bmp", "gif"],
    "switch_mode": "daily_random",
    "daily_time": "08:00",
    "interval_minutes": None,
    "skip_list": [],
    "favorites": [],
    "enable_notifications": True,
    # G03：默认**关闭**开机自启 —— 首启不静默写注册表（用户未授权）。
    # 只改默认值；_merge_with_defaults 会让用户配置里的 true 覆盖默认，
    # 故老用户显式开启过的设置不受影响。
    "auto_start": False,
    "minimize_to_tray": True,
    "log_level": "INFO",
    # 逐屏壁纸（#7）：unified=所有显示器统一一张；per_monitor=每屏一张
    "wallpaper_mode": "unified",
    "per_monitor_map": {},
}

VALID_SWITCH_MODES = ("daily_random", "interval_minutes", "manual")

# 配置存储根目录（统一到用户目录，避免打包/开机自启时 CWD 变化导致读写不同配置）
APP_DIR = Path.home() / ".wallpace"
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"

# 旧版本默认配置位置（CWD / 项目根下的遗留 json），用于首次启动时的平滑迁移。
# 注意：历史上有两种拼写 —— 错误的 `.wallspace.json` 与正确的 `.wallpace.json`。
# 早期只列了错误拼写，导致存了正确拼写文件的用户配置读不到（P0-4 附带问题）。
# 仅在使用默认路径（未显式指定 config_path）时才检查这些位置，避免污染测试隔离。
LEGACY_CONFIG_PATHS = [
    Path.cwd() / ".wallpace.json",
    Path.cwd() / ".wallspace.json",
    Path(__file__).resolve().parent.parent / ".wallpace.json",
    Path(__file__).resolve().parent.parent / ".wallspace.json",
]


class Settings:
    """应用配置管理器。

    管理配置文件的读写，支持热加载和验证。
    首次运行时无配置文件会自动生成默认配置；使用默认路径且新位置不存在时，
    会尝试从旧位置（CWD / 项目根下的 .wallpace.json / .wallspace.json）
    平滑迁移，保证用户既有配置（尤其是 image_directories）不丢失。
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """初始化配置管理器。

        Args:
            config_path: 配置文件路径；如未提供则使用用户目录下的 config.json
                （%APPDATA%/.wallpace/config.json），首次启动会自动从旧位置迁移。
        """
        self._using_default = config_path is None
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self._data: dict = {}
        self._dirty = False  # 是否存在 defer 后尚未落盘的改动
        # G04：加载异常（配置损坏已备份/重置）时的可读提示；正常加载时为 None。
        self.load_warning: str | None = None
        self.load()

    # ==================== 公开方法 ====================

    def load(self) -> dict:
        """加载配置文件，不存在则回退到默认值。

        若使用默认路径且默认配置文件尚不存在，会尝试从旧位置
        （CWD / 项目根下的 .wallpace.json / .wallspace.json）平滑迁移，
        以保证用户既有配置不丢失。

        Returns:
            当前配置的字典。
        """
        # 每次加载先清空警告：正常加载不得残留上一次损坏的提示（防误报）。
        self.load_warning = None
        try:
            if self.config_path.exists():
                with open(self.config_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    logger.warning("配置根对象必须是对象，使用默认配置")
                    raw = {}
                errors = self.validate(raw)
                if errors:
                    logger.warning("配置部分字段无效，自动修复: %s", errors)
                raw = self._merge_with_defaults(raw)
                self._data = raw
                logger.info("已从 %s 加载配置", self.config_path.name)
            else:
                if self._using_default and self._try_migrate():
                    # 已从旧位置迁移到新默认路径，落盘以固化
                    self.save()
                    logger.info("已从旧配置迁移至 %s", self.config_path.name)
                else:
                    self._data = dict(DEFAULT_CONFIG)
                    logger.info("配置文件不存在，使用默认配置")
        except json.JSONDecodeError as exc:
            logger.error("配置文件格式错误: %s", exc)
            self._recover_from_corruption("文件格式错误")
        except OSError as exc:
            logger.error("无法读取配置文件: %s", exc)
            self._recover_from_corruption("文件无法读取")
        return self._data

    def _try_migrate(self) -> bool:
        """尝试从旧版配置位置迁移数据到当前配置路径。

        仅检查 LEGACY_CONFIG_PATHS 中第一个可读的合法 JSON 文件，读取并合并为
        当前内存数据（不删除旧文件）。成功返回 True，否则 False。

        Returns:
            是否成功迁移。
        """
        for legacy in LEGACY_CONFIG_PATHS:
            if not legacy.exists():
                continue
            try:
                with open(legacy, encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("读取旧配置失败: %s", legacy)
                continue
            if not isinstance(raw, dict):
                continue
            errors = self.validate(raw)
            if errors:
                logger.warning("迁移配置存在无效字段，自动修复: %s", errors)
            self._data = self._merge_with_defaults(raw)
            return True
        return False

    def save(self, data: dict | None = None) -> None:
        """持久化配置到磁盘（原子写）。

        先写同目录临时文件、fsync 落盘，再用 os.replace 原子替换正式文件。
        这样即便写到一半崩溃/断电，也只会残留一个 .tmp，正式配置永远是
        「上一份完整内容」—— 避免被截断成非法 JSON 导致用户的图片目录、
        收藏、跳过列表整体丢失。

        Args:
            data: 要保存的配置字典；None 表示保存当前内存数据。
        """
        target = data if data is not None else self._data
        tmp_path = self.config_path.with_name(self.config_path.name + ".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(target, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_path)
            self._dirty = False
            logger.debug("配置已保存到 %s", self.config_path.name)
        except OSError as exc:
            logger.error("写入配置失败: %s", exc)
            # 清理可能残留的临时文件，避免污染目录
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"无法写入配置文件: {exc}") from exc

    def get(self, key: str, default: Any = None) -> Any:
        """获取单个配置项的值。

        Args:
            key: 配置键名，例如 'switch_mode'。
            default: 键不存在时的返回值。

        Returns:
            对应值，不存在则返回 default。
        """
        return self._data.get(key, default)

    def set(self, key: str, value: Any, *, defer: bool = False) -> None:
        """设置一个配置项。

        Args:
            key: 配置键名。
            value: 新值。
            defer: True 时只更新内存并标记「待落盘」，不立即写盘 —— 适合
                连续修改多处配置的场景，改完后调用 flush() 一次性落盘。
                默认 False，保持「设置即持久化」的既有语义。

        Note:
            使用 defer=True 后必须确保最终调用 flush()（或任意一次非 defer 的
            set/save），否则改动只存在于内存中。
        """
        self._data[key] = value
        if defer:
            self._dirty = True
            return
        self.save()

    def set_many(self, mapping: dict, *, defer: bool = False) -> None:
        """批量设置多个配置项，只触发一次磁盘写入。

        Args:
            mapping: 键值对字典。
            defer: True 时只更新内存并标记待落盘，不写盘（见 set 的说明）。
        """
        self._data.update(mapping)
        if defer:
            self._dirty = True
            return
        self.save()

    def flush(self) -> bool:
        """把延迟（defer）的修改落盘。

        Returns:
            True 表示确有改动被写入；False 表示没有待落盘的改动（空操作）。
        """
        if not self._dirty:
            return False
        self.save()
        return True

    def reset_to_defaults(self) -> dict:
        """重置所有配置为出厂默认值，并立即保存。

        Returns:
            重置后的默认配置副本。
        """
        self._data = dict(DEFAULT_CONFIG)
        self.save()
        logger.info("配置已恢复为默认值")
        return dict(self._data)

    @staticmethod
    def validate(data: dict) -> list[str]:
        """校验配置合法性，不修改数据。

        Args:
            data: 待校验的配置字典。

        Returns:
            错误消息列表；空列表表示校验通过。
        """
        errors: list[str] = []
        mode = data.get("switch_mode")

        # --- image_directories ---
        dirs = data.get("image_directories")
        if not isinstance(dirs, list):
            errors.append("'image_directories' 必须是列表")
        else:
            for d in dirs:
                if not isinstance(d, str) or not d.strip():
                    errors.append("'image_directories' 中的路径不能为空字符串")

        # --- switch_mode ---
        if mode not in VALID_SWITCH_MODES:
            errors.append(
                f"'switch_mode' 必须为 {', '.join(VALID_SWITCH_MODES)} 之一"
            )

        # --- daily_time (仅 daily_random 模式检查) ---
        if mode == "daily_random":
            time_str = str(data.get("daily_time", ""))
            parts = time_str.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                errors.append("'daily_time' 格式应为 HH:MM")
            elif not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                errors.append("'daily_time' 超出 00:00-23:59 范围")

        # --- interval_minutes ---
        interval = data.get("interval_minutes")
        if interval is not None:
            if not isinstance(interval, int) or interval <= 0:
                errors.append("'interval_minutes' 必须是正整数或 null")

        # --- skip_list / favorites ---
        for field in ("skip_list", "favorites"):
            val = data.get(field)
            if not isinstance(val, list):
                errors.append(f"'{field}' 必须是列表")

        # --- boolean flags ---
        for field in ("enable_notifications", "auto_start", "minimize_to_tray"):
            val = data.get(field)
            if not isinstance(val, bool):
                errors.append(f"'{field}' 必须是布尔值")

        # --- log_level ---
        if str(data.get("log_level", "INFO")).upper() not in (
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
        ):
            errors.append("'log_level' 必须是合法日志级别")

        # --- wallpaper_mode / per_monitor_map（#7 逐屏壁纸） ---
        # 用带默认值的 get 查询，避免对"未含新键的旧配置/局部字典"误报错误。
        if data.get("wallpaper_mode", "unified") not in ("unified", "per_monitor"):
            errors.append("'wallpaper_mode' 必须为 unified 或 per_monitor")
        if not isinstance(data.get("per_monitor_map", {}), dict):
            errors.append("'per_monitor_map' 必须是字典")

        return errors

    # ==================== 私有方法 ====================

    def _recover_from_corruption(self, reason: str) -> None:
        """配置损坏时的恢复：先备份原文件，再重置默认值并记录可读警告（G04）。

        原行为只把 ``_data`` 重置为默认值并 ``logger.warning`` 一行——用户的
        目录列表/收藏/跳过列表全部丢失，打包成 ``--windowed`` 后连日志都看不到。
        现在把损坏文件备份下来（可人工找回），并通过 ``load_warning`` 暴露给
        UI（由 ``main.py`` 复用 #15 的 ``show_platform_warning`` 展示）。
        """
        backup = self._backup_corrupt_config()
        self._data = dict(DEFAULT_CONFIG)
        if backup is not None:
            self.load_warning = (
                f"配置文件已损坏（{reason}），原文件已备份到 {backup}，"
                "并已重置为默认配置。"
            )
        else:
            self.load_warning = (
                f"配置文件已损坏（{reason}），且备份失败，已重置为默认配置。"
            )
        logger.warning("%s", self.load_warning)

    def _backup_corrupt_config(self) -> Path | None:
        """把损坏的配置文件备份为 ``config.json.corrupt-YYYYMMDD-HHMMSS``。

        - 内容与原文件逐字节一致（``shutil.copy2``）。
        - 同一秒内多次损坏时追加 ``-1``/``-2`` 后缀，保证互不覆盖。
        - 备份失败**返回 None 而不抛出**，调用方继续走重置逻辑（宁可丢备份，
          也不能因备份失败让程序起不来）。

        Returns:
            备份文件路径；失败时为 None。
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = self.config_path.with_name(
            f"{self.config_path.name}.corrupt-{timestamp}"
        )
        dest = base
        counter = 1
        while dest.exists():
            dest = base.with_name(f"{base.name}-{counter}")
            counter += 1
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.config_path, dest)
            logger.warning("已备份损坏的配置文件到 %s", dest)
            return dest
        except OSError as exc:
            logger.error(
                "备份损坏配置失败(%s)，将继续重置为默认值: %s",
                self.config_path,
                exc,
            )
            return None

    def _merge_with_defaults(self, partial: dict) -> dict:
        """用默认值修复 partial 中无效的字段。

        只在 partial 中的字段通过本地校验时才保留其值；否则回退到默认值。
        未知字段会被保留，避免丢失自定义配置。
        """
        merged = dict(DEFAULT_CONFIG)
        for k, v in partial.items():
            if k not in merged:
                merged[k] = v
                continue

            candidate = dict(DEFAULT_CONFIG)
            if k == "interval_minutes" and v is not None:
                try:
                    candidate[k] = int(v)
                except (ValueError, TypeError):
                    candidate[k] = v
            elif k in ("enable_notifications", "auto_start", "minimize_to_tray") and isinstance(v, str):
                lower = v.lower()
                if lower in {"true", "false"}:
                    candidate[k] = lower == "true"
                else:
                    candidate[k] = v
            else:
                candidate[k] = v

            if self.validate(candidate) == []:
                merged[k] = candidate[k]
            else:
                merged[k] = dict(DEFAULT_CONFIG)[k]

        return merged

    @property
    def raw(self) -> dict:
        """只读快照，防止外部直接修改内部状态。"""
        return dict(self._data)
