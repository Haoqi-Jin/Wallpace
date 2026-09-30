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
      这里**不做自动清理**：本开发环境的 safe-delete 包装器对批量删除会抛
      ``SystemExit``（BaseException，常规 ``except Exception`` 拦不住），
      曾在 configure 阶段把整个 pytest 直接打断（内部错误退出码 3）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_BASETEMP_ROOT = _REPO_ROOT / ".pytest-tmp"


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
