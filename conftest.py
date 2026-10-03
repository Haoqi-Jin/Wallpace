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
       错误均为 fixture setup 阶段的 ``OSError [WinError 53]`` /
       ``[safe-delete] trash-failed``。换成两个唯一 basetemp 并发跑同一子集 →
       两边都干净（44 passed × 2）。→ 根因是"并发 + 共享固定 basetemp"。
    ② pytest 默认 basetemp（``%TEMP%/pytest-of-<user>/...``，本机 = C:\\msys2-tmp）：
       能跑完（250 passed），但会话退出时 ``garbage-*`` 轮转清理会一次性删除
       211 个文件，被本开发环境的 safe-delete 包装器（>50 文件需二次确认）拦截
       并抛 OSError，导致进程退出码 = 1。连跑两轮实测：一轮 exit 0、一轮 exit 1，
       不稳定，不能作为默认。
    结论：改用"仓库内 ``.pytest-tmp/`` + 每次运行唯一（pid + 时间戳）"：
        - 唯一化 → 从根上消除并发冲突（**这是本文件保留的唯一理由**）；
        - 显式 basetemp → pytest 不做 garbage 轮转清理，规避 safe-delete 干扰；
        - 仍在仓库内 → 规避 %TEMP% 含非 ASCII（靳浩祺）与外部清理的不确定性。

为什么**不做**自动清理历史 ``run-*``（曾实现，后回退）
    曾经的顾虑是"显式 basetemp 不会被 pytest 清理，run-* 会无限堆积"。实测证明
    这个回收本身就是**病理性的慢**，且代价远大于收益：

        同一台机、同一命令 ``pytest --collect-only``，只切换清理开关：
            关闭自动清理 →  **1.90s**（265 tests collected）
            开启自动清理 →  **170s 内跑不完，被杀**

    机理：每个 ``run-*`` 约 205 个文件，超过本机 safe-delete 包装器 50 文件的
    批量删除阈值 → 抛 ``SystemExit``。虽然被 ``except BaseException`` 吞掉
    （确实不会打断测试），但包装器在**失败路径上会重试**，十来个目录的重试
    叠加 ≈ 数分钟，**全部耗在 conftest 的 configure 阶段**，导致门禁根本跑不动。
    注意"每次最多删 N 个"并不能解决：单目录就约 1 分钟，限量只是把常数变小，
    没有消除"在启动路径上做批量 IO"这个病根。

    判据：为一个 gitignored 的缓存目录（堆积只占磁盘、可随时手动删），
    代价是"每次测试运行可能整体卡死"——这笔账不划算。

    **手工清理方式**（唯一推荐方式）：
        rm -rf .pytest-tmp

    唯一性由本文件在 configure 阶段保证（每次运行分配新的 run-<pid>-<ts> 目录），
    所以**不需要**固定的 ``--basetemp``；``pytest.ini`` 中也不应再出现它。
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
