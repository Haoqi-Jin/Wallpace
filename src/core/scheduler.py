"""src/core/scheduler.py — 壁纸切换调度器。

控制壁纸自动/手动切换的时间策略。支持三种模式：
- daily_random: 每天指定时间随机切换一张
- interval_minutes: 每隔 N 分钟自动切换
- manual: 只响应手动触发

基于 PySide6 QTimer 实现，与 UI 线程生命周期一致。
遵循 docs/api-design.md Section 3 接口定义。
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: nocover
    # QTimer 只在注解里被引用，运行期不求值（函数体内属性注解不求值），
    # 故放 TYPE_CHECKING 即可满足 F821/mypy。
    # 刻意**不**加顶层 Qt 导入：本文件 _start_timer/_pause_timers（第 286/295 行）
    # 采用函数内延迟导入 QTimer，顶层导入会改变导入期行为并可能引入循环导入。
    from PySide6.QtCore import QTimer

    from src.core.image_library import ImageLibrary
    from src.core.wallpaper_manager import WallpaperManager

logger = logging.getLogger(__name__)

VALID_MODES = ("daily_random", "interval_minutes", "manual")


class Scheduler:
    """壁纸切换调度器。

    封装自动切换逻辑：根据配置的模式（每日随机 / 间隔 / 手动），
    在正确的时间调用回调函数完成壁纸切换。
    """

    SWITCH_MODE_DAILY = "daily_random"
    SWITCH_MODE_INTERVAL = "interval_minutes"
    SWITCH_MODE_MANUAL = "manual"

    def __init__(
        self,
        mode: str = SWITCH_MODE_DAILY,
        daily_time: str = "08:00",
        interval_minutes: int | None = None,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"无效模式 '{mode}'，应为 {', '.join(VALID_MODES)}"
            )

        self._mode: str = mode
        self._daily_time_str: str = daily_time
        self._daily_time: dt_time = self._parse_time(daily_time)
        self._interval_minutes: int | None = interval_minutes
        self._is_running: bool = False
        self._is_paused: bool = False

        # PySide6 QTimer 实例
        self._timer: QTimer | None = None
        self._on_switch: Callable[[str], None] | None = None
        self._library: ImageLibrary | None = None
        self._wallpaper_manager: WallpaperManager | None = None
        # 可注入的壁纸设置器（#7 逐屏模式分流用）。None = 回落 WallpaperManager。
        self._wallpaper_applier: Callable[[str], bool] | None = None

        logger.info(
            "Scheduler 初始化: mode=%s, daily=%s, interval=%s",
            mode, daily_time, interval_minutes,
        )

    # ==================== 公开方法 ====================

    def set_dependencies(
        self,
        library: "ImageLibrary",
        wallpaper_manager: "WallpaperManager",
    ) -> None:
        self._library = library
        self._wallpaper_manager = wallpaper_manager

    def set_wallpaper_applier(
        self, func: Callable[[str], bool] | None
    ) -> None:
        """注入自定义的壁纸设置器（可选）。

        默认 ``None`` = 保持原有行为（直接调用 ``WallpaperManager.set_wallpaper``）。
        MainWindow 会注入一个"按模式分流"的实现：逐屏模式走各显示器独立分配，
        统一模式走整屏设置 —— 这样 Scheduler 无需依赖 GUI，也能避免"先统一设一张
        再被逐屏覆盖"的双写与闪烁。

        Args:
            func: 接收图片路径、返回是否成功的回调；``None`` 表示恢复默认实现。
        """
        self._wallpaper_applier = func

    def _apply_wallpaper(self, image_path: str) -> bool:
        """设置壁纸：优先用注入的 applier，否则回落 WallpaperManager。

        注入实现若抛异常，则记录日志并回落默认实现（绝不中断调度流程）。

        Args:
            image_path: 图片绝对路径。

        Returns:
            是否设置成功。
        """
        if self._wallpaper_applier is not None:
            try:
                return bool(self._wallpaper_applier(image_path))
            except Exception:
                logger.warning("注入的壁纸设置回调异常，回落默认实现", exc_info=True)
        if self._wallpaper_manager is None:
            return False
        return self._wallpaper_manager.set_wallpaper(image_path)

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        old_mode = self._mode
        if value not in VALID_MODES:
            raise ValueError(f"无效模式 '{value}'")
        self._mode = value
        logger.info("模式变更: %s -> %s", old_mode, value)

        if (old_mode != "manual") != (value != "manual"):
            if self._is_running and not self._is_paused:
                self.stop()
                self.start(self._on_switch)
            elif self._is_running:
                self.stop()
                self.resume()

    def start(self, on_switch: Callable[[str], None] | None = None) -> None:
        if self._is_running and not self._is_paused:
            logger.warning("调度器已在运行，忽略 start()")
            return

        if self._is_paused:
            self._resume_timers()
            return

        self._on_switch = on_switch
        self._is_running = True
        self._is_paused = False

        if self._mode == self.SWITCH_MODE_DAILY:
            self._start_daily_timer()
        elif self._mode == self.SWITCH_MODE_INTERVAL:
            self._start_interval_timer()
        else:
            logger.info("手动模式: 不启动自动定时器")

    def trigger_now(self) -> None:
        if self._library is None or self._wallpaper_manager is None:
            logger.error("调度器未注入 library/wallpaper_manager")
            return

        image_path = self._library.get_random()
        if image_path is None:
            logger.warning("没有可用图片可切换")
            return

        success = self._apply_wallpaper(image_path)
        if success:
            logger.info("手动触发切换: %s", Path(image_path).name)
            if self._on_switch:
                self._on_switch(image_path)
        else:
            logger.error("手动切换失败")

    def pause(self) -> None:
        if not self._is_running:
            logger.warning("调度器未运行，无法暂停")
            return
        self._is_paused = True
        self._pause_timers()
        logger.info("调度器已暂停")

    def resume(self) -> None:
        if not self._is_running:
            logger.warning("调度器未运行")
            return
        if not self._is_paused:
            logger.warning("调度器未在暂停状态")
            return
        self._is_paused = False
        self._resume_timers()
        logger.info("调度器已恢复")

    def stop(self) -> None:
        self._is_running = False
        self._is_paused = False
        self._pause_timers()
        logger.info("调度器已停止")

    @property
    def is_active(self) -> bool:
        return self._is_running and not self._is_paused

    @property
    def is_paused(self) -> bool:
        return self._is_running and self._is_paused

    @property
    def is_running(self) -> bool:
        """返回调度器是否正在运行（包括已暂停状态）。"""
        return self._is_running

    @property
    def interval_minutes(self) -> int | None:
        """返回当前设置的间隔分钟数（仅 interval 模式有意义）。"""
        return self._interval_minutes

    def set_interval_minutes(self, minutes: int | None) -> None:
        """更新间隔分钟数；若正在以间隔模式运行，则重建定时器立即生效。

        此前 UI 层只能直写 `scheduler._interval_minutes`（绕过定时器重建），
        导致"改了值但定时器没变"与"stop/start 后才生效"两条路径行为不一致。
        这里统一为唯一入口。

        Args:
            minutes: 间隔分钟数，必须为正整数。

        Raises:
            ValueError: minutes 不是正整数时抛出。
        """
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes <= 0
        ):
            raise ValueError(f"interval_minutes 必须 > 0，收到 {minutes!r}")
        changed = self._interval_minutes != minutes
        self._interval_minutes = minutes
        if changed:
            logger.info("间隔时间更新为 %d 分钟", minutes)
        if (
            changed
            and self._is_running
            and not self._is_paused
            and self._mode == self.SWITCH_MODE_INTERVAL
        ):
            # 重建定时器，让新间隔立即生效
            self._start_interval_timer()

    # ==================== 私有方法 ====================

    def _parse_time(self, time_str: str) -> "dt_time":
        try:
            h, m = map(int, time_str.split(":"))
            return dt_time(h, m)
        except (ValueError, AttributeError) as e:
            raise ValueError(f"无效时间格式 '{time_str}'，应为 HH:MM") from e

    def _start_daily_timer(self) -> None:
        now = datetime.now()
        target_today = now.replace(
            hour=self._daily_time.hour,
            minute=self._daily_time.minute,
            second=0, microsecond=0,
        )
        if target_today <= now:
            target_today += timedelta(days=1)

        delay_ms = int((target_today - now).total_seconds() * 1000)
        logger.info(
            "每日模式: 距离下次切换 %.1f 秒 (%d ms)",
            delay_ms / 1000, delay_ms,
        )

        self._schedule_once(delay_ms, self._do_daily_timer)

    def _start_interval_timer(self) -> None:
        if self._interval_minutes is None or self._interval_minutes <= 0:
            raise ValueError("interval_minutes 必须 > 0")
        delay_ms = self._interval_minutes * 60 * 1000
        logger.info(
            "间隔模式: 每 %d 分钟切换一次 (%d ms)",
            self._interval_minutes, delay_ms,
        )
        self._start_repeating_timer(delay_ms, self._do_interval_switch)

    def _schedule_once(self, delay_ms: int, callback: Callable) -> None:
        from PySide6.QtCore import QTimer
        self._pause_timers()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(delay_ms)
        self._timer = timer

    def _start_repeating_timer(self, delay_ms: int, callback: Callable) -> None:
        from PySide6.QtCore import QTimer
        self._pause_timers()
        timer = QTimer()
        timer.timeout.connect(callback)
        timer.start(delay_ms)
        self._timer = timer

    def _resume_timers(self) -> None:
        if self._mode == self.SWITCH_MODE_DAILY:
            self._start_daily_timer()
        elif self._mode == self.SWITCH_MODE_INTERVAL:
            self._start_interval_timer()

    def _pause_timers(self) -> None:
        """停止并释放当前定时器（幂等、可重入安全）。

        定时器可能已在 C++ 侧被 Qt 析构（例如事件循环已处理过 deleteLater），
        此时 stop() 与 deleteLater() **都会**抛 RuntimeError，故一并纳入保护。
        先把引用摘掉再操作，避免异常路径下留下悬空引用导致重复释放。
        """
        timer = self._timer
        self._timer = None
        if timer is None:
            return
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            # C++ 对象已被析构，无需（也无法）再操作
            pass
        except Exception:
            # 兜底：不让清理路径的意外异常冒泡中断 stop()/pause() 流程
            logger.warning("释放定时器时出现意外错误", exc_info=True)

    def _do_daily_timer(self) -> None:
        if self._library is None or self._wallpaper_manager is None:
            logger.warning("library/wallpaper_manager 未就绪")
            return

        image_path = self._library.get_random()
        if image_path is None:
            logger.warning("无可用的图片")
            return

        success = self._apply_wallpaper(image_path)
        if success:
            logger.info("每日自动切换: %s", Path(image_path).name)
            if self._on_switch:
                self._on_switch(image_path)

        if self._is_running and not self._is_paused:
            self._start_daily_timer()

    def seconds_until_next(self) -> int | None:
        """距下次自动切换的剩余秒数；未运行/已暂停/无定时器 → None。

        统一覆盖 daily_random 与 interval_minutes 两种模式（都走 ``self._timer``）。

        实测结论（PySide6 6.x / Python 3.13 / Windows / offscreen）：
        ``QTimer.remainingTime()`` 对**单次与重复定时器**都返回「距下次触发的
        毫秒数」，且**重复定时器每次触发后会重置为完整周期**：
          * 单次 2000ms：start→2000、+300ms→1697、+1800ms→193、触发后→**-1**
          * 重复 300ms：start→300、+150ms→144、触发后→253、再 +250ms→2
        因此：
          * ``ms < 0``（定时器未激活 / 单次已触发）视为「无有效倒计时」→ None，
            避免把 -1 当成 "0 秒" 显示；
          * 否则返回 ``ms // 1000``（整秒向下取整）。
        暂停 → ``_pause_timers()`` 已把 ``_timer`` 置 None，故返回 None，
        「暂停即冻结」由本方法保证。

        Returns:
            剩余秒数（>=0）；无有效倒计时返回 None。
        """
        if not self._is_running or self._is_paused or self._timer is None:
            return None
        try:
            ms = self._timer.remainingTime()
        except RuntimeError:
            # 定时器底层 C++ 对象可能已被析构
            return None
        if ms < 0:
            return None
        return ms // 1000

    def get_next_switch_time(self) -> datetime | None:
        """Return the next scheduled switch time (daily_random mode only).

        Returns None if scheduler is not running or not in daily mode.
        """
        if self._mode != self.SWITCH_MODE_DAILY or not self._is_running:
            return None
        now = datetime.now()
        target = now.replace(
            hour=self._daily_time.hour,
            minute=self._daily_time.minute,
            second=0, microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        return target

    def _do_interval_switch(self) -> None:
        if self._library is None or self._wallpaper_manager is None:
            logger.warning("library/wallpaper_manager 未就绪")
            return

        image_path = self._library.get_random()
        if image_path is None:
            logger.warning("无可用的图片")
            return

        success = self._apply_wallpaper(image_path)
        if success:
            logger.info("间隔自动切换: %s", Path(image_path).name)
            if self._on_switch:
                self._on_switch(image_path)
