"""src/main.py — Wallpace 入口点。

用法:
    python src/main.py          # 普通启动
    python src/main.py --hidden # 开机自启模式（不显示主窗口）
    python src/main.py --test   # 只运行核心模块测试
"""

import contextlib
import logging
import logging.handlers
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 日志配置 — 使用用户 home 目录避免权限问题。
# G07：路径收敛到 diagnostics.LOGS_DIR（单一真源），UI 的「打开日志目录」
# 诊断入口与这里写文件的是同一个路径，不会出现"入口打开的目录≠日志所在"。
from src.core.diagnostics import LOGS_DIR

LOG_DIR = LOGS_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 卡死时自动把主线程堆栈 dump 到日志，便于排查 UI 无响应类问题
import faulthandler

with contextlib.suppress(OSError):
    # 刻意**不**用 with open(...)：faulthandler 捕获的是文件描述符，
    # with 块退出即 close，此后崩溃就再也写不进 traceback（实测 fd 失效，
    # os.fstat -> OSError 9）。这正是本行要保留的文件句柄常开写法。
    # 已实测：faulthandler 内部持有该文件对象的引用，故此处用临时形式打开是
    # 安全的（对象被回收后 fd 仍有效）；改用 with open(...) 反而会在块退出时
    # close，导致崩溃 traceback 写入失败（os.fstat -> OSError 9）。
    faulthandler.enable(file=open(LOG_DIR / "traceback.log", "w"))  # noqa: SIM115
logging.basicConfig(
    level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        # G06：轮转日志。托盘常驻数月，FileHandler 只增不减会让 wallpace.log
        # 无限膨胀；这里 1MB × 3 个备份封顶（约 4MB），旧日志按序号保留可查。
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "wallpace.log",
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stderr),
    ],
)

logger = logging.getLogger("main")


def run_tests() -> None:
    """仅运行核心模块单元测试，退出前打印汇总。"""
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n所有测试通过！")
    else:
        print(
            f"\n失败 {len(result.failures)} 项，跳过 "
            f"{len(result.skipped)} 项"
        )
    sys.exit(0 if result.wasSuccessful() else 1)


def _resolve_interval_minutes(settings, switch_mode: str):
    """校验并修正启动时的 interval_minutes 配置。

    历史缺陷（P0-2）：设置页切到「间隔时间」时给 switch_mode 做了持久化，
    却没把兜底出来的 interval_minutes 写回配置，于是配置可能是
    `{switch_mode: "interval_minutes", interval_minutes: null}`，下次启动
    `Scheduler.start()` 直接抛 ValueError，打包成 --windowed 后表现为
    "双击图标 → 无声退出"。这里在构造 Scheduler 之前统一修正并落盘。

    Args:
        settings: Settings 实例。
        switch_mode: 配置中的切换模式。

    Returns:
        修正后的 interval_minutes（可能是 None，表示非间隔模式/保持原样）。
    """
    interval_minutes = settings.get("interval_minutes", None)
    if switch_mode != "interval_minutes":
        return interval_minutes
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, int)
        or interval_minutes <= 0
    ):
        logger.warning(
            "配置的 interval_minutes 无效(%r)，已回退为 60", interval_minutes
        )
        interval_minutes = 60
        try:
            settings.set("interval_minutes", interval_minutes)
        except Exception:
            logger.exception("回写 interval_minutes 失败，仅本次启动生效")
    return interval_minutes


def _cleanup_stale_autostart(mgr) -> bool:
    """启动自愈（G02）：清理指向已不存在可执行文件的坏开机自启项。

    删掉 wallpace.exe / 换目录重装后，HKCU 里残留的旧路径会变成坏启动项
    （开机静默报错、任务管理器里可见）。有效项不动，避免误删。

    Args:
        mgr: AutostartManager 实例（或其替身）。

    Returns:
        确实清理了一项返回 True；无自启项或自启项仍有效时返回 False。
    """
    if not mgr.is_enabled:
        return False
    if not mgr.is_valid():
        logger.warning("检测到开机自启项指向已不存在的可执行文件，自动清理")
        return mgr.cleanup_invalid()
    return False


def start_scheduler(scheduler, on_switch) -> bool:
    """启动调度器；任何 ValueError 都降级为手动模式，绝不阻止主窗口显示。

    Args:
        scheduler: Scheduler 实例。
        on_switch: 切换完成后的回调。

    Returns:
        True 表示按原模式启动成功；False 表示已降级为手动模式。
    """
    try:
        scheduler.start(on_switch=on_switch)
        return True
    except ValueError as exc:
        logger.error("调度器启动失败，已降级为手动模式: %s", exc)
        try:
            scheduler.stop()
            scheduler.mode = "manual"
            scheduler.start(on_switch=on_switch)
        except Exception:
            logger.exception("降级为手动模式后仍无法启动调度器，忽略")
        return False


def main() -> None:
    """程序入口。"""
    if "--test" in sys.argv:
        run_tests()
        return

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Wallpace")
    app.setOrganizationName("wallpace")

    # G01：单实例保护。必须在创建 MainWindow（及其调度器/目录监听/托盘）之前，
    # 否则第二实例会先建好窗口再退出，出现"窗口闪一下"甚至短暂抢设壁纸。
    # 所有失败路径都降级为"允许启动"（见 SingleInstanceGuard），绝不阻断用户。
    from src.core.single_instance import SingleInstanceGuard

    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        # 已有实例在运行：请求它把窗口拉到前台，然后静默退出（退出码 0）。
        guard.notify_existing()
        print("Wallpace 已在运行，已请求激活已有窗口。")
        sys.exit(0)

    # Phase 1: 核心模块初始化
    from src.core.image_library import ImageLibrary
    from src.core.settings import Settings
    from src.core.wallpaper_manager import WallpaperManager

    settings = Settings()

    # 日志级别按配置调整（--debug 命令行参数强制 DEBUG 优先）
    _level_name = str(settings.get("log_level", "INFO")).upper()
    _level = getattr(logging, _level_name, logging.INFO)
    if "--debug" in sys.argv:
        _level = logging.DEBUG
    logging.getLogger().setLevel(_level)

    library = ImageLibrary(
        directories=settings.get("image_directories"),
        extensions=settings.get("extensions"),
    )
    wm = WallpaperManager()

    logger = logging.getLogger("main")

    # 验证壁纸管理器是否可用
    if not wm.is_supported:
        logger.error("当前平台不支持壁纸设置操作")

    # Phase 3: 调度器初始化
    from src.core.scheduler import Scheduler

    switch_mode = settings.get("switch_mode", "daily_random")
    daily_time = settings.get("daily_time", "08:00")
    # P0-2：进入间隔模式前先修正并持久化 interval_minutes，避免
    # {switch_mode: "interval_minutes", interval_minutes: null} 导致启动崩溃
    interval_minutes = _resolve_interval_minutes(settings, switch_mode)

    scheduler = Scheduler(
        mode=switch_mode,
        daily_time=daily_time,
        interval_minutes=interval_minutes,
    )
    scheduler.set_dependencies(library, wm)

    # 同步开机自启注册表（仅在状态不一致时写，避免每次启动都写）
    from src.core.autostart_registry import AutostartManager

    mgr = AutostartManager()
    # G02：启动自愈——坏自启项（指向已删除/exe 换位置的注册表残留）在此清理。
    _cleanup_stale_autostart(mgr)
    # 兜底默认值必须是 False：与 DEFAULT_CONFIG 一致。若配置缺该键（老版本
    # 配置/手改配置），True 会悄悄恢复"首启静默写注册表"这个已被 G03 修掉的
    # 行为 —— 典型的"兜底值把修复吃掉"回归。
    want = settings.get("auto_start", False)
    if want and not mgr.is_enabled:
        mgr.enable()
    elif not want and mgr.is_enabled:
        mgr.disable()

    # Phase 2: 创建 MainWindow
    from src.app.window import MainWindow

    window = MainWindow(
        settings=settings,
        library=library,
        wallpaper_manager=wm,
        scheduler=scheduler,
    )
    # G01：作为首实例，收到第二实例的 activate 消息时把主窗口拉到前台。
    guard.set_activate_callback(window.bring_to_front)
    if "--hidden" not in sys.argv:
        window.show()

    # G04：配置损坏时已在 Settings 内备份并重置，这里把警告显式告知用户
    # （否则用户的目录/收藏丢失而毫无感知，打包后连日志都看不到）。
    # 复用 #15 建好的 show_platform_warning 机制，不自造通知方式。
    if getattr(settings, "load_warning", None):
        window.show_platform_warning(settings.load_warning)

    # 平台能力提示：非 Windows 下壁纸设置不可用，必须在 UI 上明确告知
    # （此前只记日志，打包后无控制台可看，用户会以为程序坏了）
    if not wm.is_supported:
        window.show_platform_warning(
            "当前平台不支持设置系统壁纸（仅 Windows 可用），"
            "「换一张」等切换操作将不会生效。"
        )

    # G08：切换彻底失败（候选全试过）时托盘气泡兜底提示，避免静默"点了没反应"
    scheduler.set_switch_failure_handler(window.notify_switch_failed)

    logger.info("Wallpace v%s 启动", __import__("src").__version__)
    logger.info(
        "配置: %d 个图片目录, %d 张扫描图片",
        library.directory_count,
        library.total_count,
    )
    logger.info("当前壁纸: %s", wm.get_current_wallpaper())
    logger.info("切换模式: %s", scheduler.mode)

    # 启动调度器（失败也不会阻止主窗口显示，见 start_scheduler）
    start_scheduler(scheduler, window.on_wallpaper_switched)
    logger.info("调度器已启动 (mode=%s)", scheduler.mode)

    print("Wallpace 正在启动...")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
