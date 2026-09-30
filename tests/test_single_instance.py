"""tests/test_single_instance.py — G01 单实例保护回归。

覆盖点（对应任务②）：
    1. 锁被占用 → 明确报告"已在运行"，且不抛异常；
    2. 空闲 → 获取 / 释放 / 再次获取；
    3. 第二实例消息 → 首实例**真实收到**（真 QLocalServer + QLocalSocket）；
    4. 降级护栏：锁内部异常 → 仍然"可以启动"；
    5. 纯逻辑兜底：不经套接字直接驱动消息处理，永不跳过（4 的 skip 兜底）。

环境说明：QLocalServer/QLocalSocket 需要一个 QCoreApplication 实例；本机
CI/无头环境下若本地套接字不可用，真实通路用例会 skip 并写明环境原因，
但纯逻辑兜底用例保证核心回调逻辑始终被验证。
"""

import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.single_instance import (  # noqa: E402
    ACTIVATE_MESSAGE,
    LOCAL_SOCKET_AVAILABLE,
    SingleInstanceGuard,
)


@pytest.fixture(scope="module")
def qapp():
    """本地套接字所需的事件循环宿主（QLocalServer 必须依附 QCoreApplication）。"""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    yield app


def _unique_server_name() -> str:
    """生成唯一本地服务名，避免并行/重复运行时互相干扰。"""
    return f"wallpace-test-{uuid.uuid4().hex}"


class _FakeSocket:
    """最小 socket 替身，用于驱动纯逻辑消息处理（不依赖真实套接字）。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def readAll(self) -> bytes:
        return self._data

    def deleteLater(self) -> None:  # 与 QLocalSocket 接口对齐
        pass


class TestLockAcquisition:
    """锁的占用判定与释放语义。"""

    def test_occupied_lock_reports_already_running(self, tmp_path):
        """锁被另一实例占用时，try_acquire() 返回 False 且不抛异常。"""
        lock_path = tmp_path / "wallpace.lock"
        first = SingleInstanceGuard(
            lock_path=lock_path, server_name=_unique_server_name()
        )
        assert first.try_acquire() is True
        try:
            second = SingleInstanceGuard(
                lock_path=lock_path, server_name=_unique_server_name()
            )
            # 关键：不是抛异常，而是明确"已有实例"信号
            assert second.try_acquire() is False
            assert second.is_primary is False
        finally:
            first.release()

    def test_free_acquire_release_reacquire(self, tmp_path):
        """空闲 → 获取成功；释放后 → 可再次获取。"""
        lock_path = tmp_path / "wallpace.lock"
        g1 = SingleInstanceGuard(
            lock_path=lock_path, server_name=_unique_server_name()
        )
        assert g1.try_acquire() is True
        assert g1.is_primary is True
        g1.release()
        assert g1.is_primary is False

        g2 = SingleInstanceGuard(
            lock_path=lock_path, server_name=_unique_server_name()
        )
        assert g2.try_acquire() is True
        g2.release()


class TestActivationMessage:
    """第二实例 → 首实例的真实消息通路。"""

    def test_second_instance_message_reaches_first(self, tmp_path, qapp):
        """真 QLocalServer + QLocalSocket：第二条消息必须被首实例收到。"""
        lock_path = tmp_path / "wallpace.lock"
        name = _unique_server_name()
        first = SingleInstanceGuard(lock_path=lock_path, server_name=name)
        received: list = []
        first.set_activate_callback(lambda: received.append("activate"))
        assert first.try_acquire() is True
        try:
            if not LOCAL_SOCKET_AVAILABLE or first._server is None:  # noqa: SLF001
                pytest.skip(
                    "QLocalServer 在本环境不可用（QtNetwork 被裁剪，或本地套接字"
                    "被禁用/无头环境无法监听），真实通路用例跳过；"
                    "核心回调逻辑由 TestActivatePureLogic 兜底验证，永不跳过。"
                )

            second = SingleInstanceGuard(lock_path=lock_path, server_name=name)
            assert second.try_acquire() is False  # 确实被判为第二实例
            assert second.notify_existing() is True

            # 驱动事件循环，等待首实例的 newConnection→readyRead 链路完成
            deadline = time.time() + 3.0
            while not received and time.time() < deadline:
                qapp.processEvents()
                time.sleep(0.01)
            assert received == ["activate"], "首实例未收到第二实例的 activate 消息"
        finally:
            first.release()


class TestActivatePureLogic:
    """不依赖套接字的纯逻辑分支——作为真实通路用例的**非跳过**兜底。"""

    def test_activate_message_triggers_callback(self):
        guard = SingleInstanceGuard(
            lock_path=Path("unused.lock"), server_name=_unique_server_name()
        )
        received: list = []
        guard.set_activate_callback(lambda: received.append("go"))
        guard._on_socket_ready(_FakeSocket(ACTIVATE_MESSAGE))  # noqa: SLF001
        assert received == ["go"]

    def test_unrelated_message_is_ignored(self):
        guard = SingleInstanceGuard(
            lock_path=Path("unused.lock"), server_name=_unique_server_name()
        )
        received: list = []
        guard.set_activate_callback(lambda: received.append("go"))
        guard._on_socket_ready(_FakeSocket(b"something-else"))  # noqa: SLF001
        assert received == []

    def test_callback_exception_does_not_propagate(self):
        """回调自身异常必须被吞掉，绝不能打断事件循环/进程。"""
        guard = SingleInstanceGuard(
            lock_path=Path("unused.lock"), server_name=_unique_server_name()
        )

        def boom() -> None:
            raise RuntimeError("callback exploded")

        guard.set_activate_callback(boom)
        # 不得抛出
        guard._on_socket_ready(_FakeSocket(ACTIVATE_MESSAGE))  # noqa: SLF001


class TestDegradationGuard:
    """所有失败路径都必须降级为"允许启动"。"""

    def test_internal_lock_error_still_allows_start(self, tmp_path, monkeypatch):
        """锁构造阶段抛异常 → try_acquire() 必须返回 True（允许启动）。"""
        guard = SingleInstanceGuard(
            lock_path=tmp_path / "wallpace.lock",
            server_name=_unique_server_name(),
        )

        def _boom():
            raise RuntimeError("lock backend down")

        monkeypatch.setattr(guard, "_make_lock_file", _boom)
        assert guard.try_acquire() is True
        assert guard.is_degraded is True

    def test_release_is_safe_when_degraded(self, tmp_path, monkeypatch):
        guard = SingleInstanceGuard(
            lock_path=tmp_path / "wallpace.lock",
            server_name=_unique_server_name(),
        )
        monkeypatch.setattr(
            guard, "_make_lock_file", lambda: (_ for _ in ()).throw(OSError("nope"))
        )
        guard.try_acquire()
        guard.release()  # 不得抛异常
