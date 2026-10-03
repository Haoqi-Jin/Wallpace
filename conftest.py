"""conftest.py（仓库根）— 为每次 pytest 运行分配"仓库内 + 每次唯一"的 basetemp。

为什么放在仓库根而不是 tests/conftest.py：
    basetemp 在 pytest 启动的 `pytest_configure` 阶段就被 ``tmp_path_factory``
    读取；``tests/conftest.py`` 属于"收集期"加载，时机太晚。只有 rootdir 下的
    conftest.py 会作为"初始 conftest"在 configure 之前加载，设置才生效。

为什么需要一个"每次唯一"的 basetemp（本机实测，2026-09，Windows 11 + PySide6）：
    ① 固定 basetemp（原 ``addopts = --basetemp=.pytest-tmp``）：
       两个 pytest 会话共享同一个固定 basetemp 时，``tmp_path_factory`` 的
       ``rm_rf(basetemp) + mkdir()`` 会互相打架。实测：并发运行两个会话，
       A 会话 44 passed 干净；B 会话崩出 ``28 passed + 16 errors``（或 30+14），
       错误均为 fixture setup 阶段的 ``OSError [Errno 53] 找不到网络路径`` /
       ``[safe-delete] trash-failed``。换成两个唯一 basetemp 并发跑同一子集 →
       两边都干净（44 passed × 2）。→ 根因是"并发 + 共享固定 basetemp"。
    ② pytest 默认 basetemp（``%TEMP%/pytest-of-<user>/...``，本机 = C:\\msys2-tmp）：
       能跑完（250 passed），但会话退出时 ``garbage-*`` 轮转清理会一次性删除
       211 个文件，被本开发环境的 safe-delete 包装器（>50 文件需二次确认）拦截
       并抛 OSError，导致进程退出码 = 1。连跑两轮实测：一轮 exit 0、一轮 exit 1，
       不稳定，不能作为默认。
    结论：改用"仓库内 ``.pytest-tmp/`` + 每次运行唯一（pid + 时间戳）"：
        - 唯一化 → 从根上消除并发冲突；
        - 显式 basetemp → pytest 不做 garbage 轮转清理，规避 safe-delete 干扰；
        - 仍在仓库内 → 规避 %TEMP% 含非 ASCII（靳浩祺）与外部清理的不确定性。

注意：显式 basetemp 不会被 pytest 在退出时删除，故历史 ``run-*`` 目录会累积，
      但均在 ``.pytest-tmp/``（已 gitignore）下，可由开发者/CI 手动清理。
      这里做**有界回收**：每次分配完本次目录后，删掉旧的 ``run-*``，只保留
      最近 3 个（含本次）。回收是纯 best-effort，**任何异常都静默吞掉**——
      本开发环境的 safe-delete 包装器对批量删除会抛 ``SystemExit``
      （BaseException，常规 ``except Exception`` 拦不住），曾在 configure 阶段
      把整个 pytest 直接打断（内部错误退出码 3）。因此这里一律用
      ``except BaseException``：清理失败最坏只是目录多留几个，**绝不影响测试**。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_BASETEMP_ROOT = _REPO_ROOT / ".pytest-tmp"

# 只认我们自己按 ``run-<pid>-<毫秒时间戳>`` 命名的目录，绝不误伤其它任何东西。
_RUN_DIR_RE = re.compile(r"run-\d+-\d+")

# 磁盘上最多保留几个 run-* （含本次）。
_KEEP_RECENT_RUNS = 3


def pytest_configure(config) -> None:
    """把本次运行的 basetemp 指向唯一的仓库内目录（用户未显式指定时）。"""
    if config.option.basetemp:
        # 命令行/其它 ini 已显式指定 basetemp，尊重之。
        return
    # pytest 对显式 basetemp 只做 ``basetemp.mkdir()``（parents=False），
    # 因此必须先把父目录 .pytest-tmp/ 建好，否则报 WinError 3。
    _BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    unique = _BASETEMP_ROOT / f"run-{os.getpid()}-{int(time.time() * 1000)}"
    config.option.basetemp = str(unique)
    # 回收旧的 run-*。此处本次目录尚未被 pytest 创建，因此只保留
    # ``_KEEP_RECENT_RUNS - 1`` 个旧的 —— 加上随后创建的本次目录，磁盘上
    # 总数正好 ≤ _KEEP_RECENT_RUNS。
    _prune_old_runs(_BASETEMP_ROOT, keep=_KEEP_RECENT_RUNS - 1)


def _prune_old_runs(root: Path, keep: int) -> None:
    """回收 ``root`` 下最旧的 ``run-*`` 目录，最多保留 ``keep`` 个（按 mtime 新旧）。

    边界（重要）：只处理 ``root`` **直属**、且目录名**严格**匹配
    ``run-<数字>-<数字>`` 的子目录；不递归、不跟随符号链接、不碰其它任何路径。
    这不是"清临时文件"，而是回收我们自己产生的垃圾。

    全程 best-effort：捕获 ``BaseException``（含 ``SystemExit``）并静默跳过，
    任何清理失败都不允许影响测试运行。
    """
    try:
        if not root.is_dir():
            return
        runs = [
            p
            for p in root.iterdir()
            if p.is_dir() and not p.is_symlink() and _RUN_DIR_RE.fullmatch(p.name)
        ]
        # mtime 降序：最新的在前。keep<=0 时全删；keep>=总数时全留。
        runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in runs[max(keep, 0) :]:
            try:
                shutil.rmtree(stale)
            except BaseException:  # noqa: BLE001, B036 —— 清理失败必须静默
                pass
    except BaseException:  # noqa: BLE001, B036 —— 兜底：绝不因清理打断 pytest
        pass

