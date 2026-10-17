"""src/core/diagnostics.py — 诊断入口（G07）。

让用户「出问题知道去哪看」：程序日志固定在 ``~/.wallpace/logs/``，
本模块提供该目录的单一真源常量与跨平台「在文件管理器中打开」动作。
此前日志位置只有 main.py 的模块级常量，UI 上无任何入口，
打包成 --windowed 后用户出问题时只能干瞪眼。
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# 日志目录的单一真源（main.py 的 FileHandler / faulthandler 也用它）。
LOGS_DIR = Path.home() / ".wallpace" / "logs"


def open_logs_directory() -> bool:
    """在系统文件管理器中打开日志目录（Windows: 资源管理器，其它: xdg-open/open）。

    目录不存在时先创建（首次运行可能尚未产生日志），确保动作永远可达。
    任何失败都**返回 False 而不抛出**，由 UI 层决定如何告知用户。

    Returns:
        成功启动文件管理器返回 True，否则 False。
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("无法创建日志目录 %s: %s", LOGS_DIR, exc)
        return False

    if os.name == "nt":
        # os.startfile：用默认程序（资源管理器）打开目录，Windows 独有
        try:
            os.startfile(LOGS_DIR)  # type: ignore[attr-defined]
            return True
        except OSError as exc:
            logger.error("打开日志目录失败: %s", exc)
            return False

    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener is None:
        logger.error("未找到 xdg-open/open，无法打开日志目录")
        return False
    try:
        subprocess.Popen([opener, str(LOGS_DIR)])
        return True
    except OSError as exc:
        logger.error("打开日志目录失败: %s", exc)
        return False
