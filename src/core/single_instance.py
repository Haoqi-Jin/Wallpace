"""src/core/single_instance.py — 单实例保护（仅依赖 QtCore，无 GUI/Widget 依赖）。

背景（G01）：
    Wallpace 可能被重复启动（用户双击图标、开机自启与手动启动叠加、快捷方式被
    连点）。多个实例会各自启动调度器与目录监听，直接后果是：

    * "抢设壁纸"——两个实例的定时器互相覆盖对方刚设好的壁纸；
    * 配置双写——并发写 config.json 可能丢改动；
    * 托盘图标叠加、日志互相穿插，用户一头雾水。

    因此需要保证同一时刻只有一个"逻辑实例"，后来者应唤醒已有实例并静默退出。

设计要点（务必遵守）：
    1. 本模块**不导入任何 GUI 模块**（只依赖 ``PySide6.QtCore`` 与非常规意义的
       ``PySide6.QtNetwork``，后者非 QtWidgets/QtGui），以便被单元测试直接导入；
    2. **所有失败路径都降级为"允许启动"**——单实例只是体验优化，绝不能因为
       锁/本地套接字自身出错，让用户彻底打不开程序；
    3. 锁文件与配置、日志同处 ``~/.wallpace/`` 目录，便于统一清理。

两级机制：
    * ``QLockFile``：跨进程互斥，判定"是否已有实例"；
    * ``QLocalServer``/``QLocalSocket``：已存在实例时，用一条 ``"activate"``
      消息把它唤到前台。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QCoreApplication, QLockFile

# QLocalServer/QLocalSocket 属于 QtNetwork（非 QtWidgets/QtGui，仍无 GUI 依赖）。
# 某些精简/裁剪的 Qt 发行版可能不含 QtNetwork，故做可选导入：缺失时锁仍然可用，
# 只是"唤醒已有实例"的套接字通路降级为不可用（绝不阻断启动）。
try:  # pragma: no cover - 取决于运行环境是否裁剪 QtNetwork
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    LOCAL_SOCKET_AVAILABLE = True
except ImportError:  # pragma: no cover
    QLocalServer = None  # type: ignore[assignment]
    QLocalSocket = None  # type: ignore[assignment]
    LOCAL_SOCKET_AVAILABLE = False

logger = logging.getLogger(__name__)

# 唤醒消息：第二实例发给首实例的命令，首实例收到后把主窗口拉到前台。
ACTIVATE_MESSAGE = b"activate"

# 默认本地服务名。带项目前缀避免与其它 Qt 程序撞名。
DEFAULT_SERVER_NAME = "wallpace-single-instance"

# 默认锁文件：与 settings/logs 同目录。
DEFAULT_LOCK_PATH = Path.home() / ".wallpace" / "wallpace.lock"

# 连接/写超时（毫秒）。仅用于第二实例"尽力唤醒"，超时即静默退出，不阻塞。
_CONNECT_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 1000

# QLockFile 陈旧锁判定时间（毫秒）。
#
# 实测（Windows 11 / PySide6，2026-09）：硬杀进程（taskkill /F）后，QLockFile
# 依赖锁文件里记录的 PID 判断拥有者已死，tryLock() **无需**等待即可抢占，因此
# 将陈旧时间设为 0（"只要拥有者进程不在，立刻视为陈旧"）可以做到强杀后立即接管。
# 见 tests/test_single_instance.py::test_stale_lock_taken_over_after_kill。
_STALE_LOCK_MS = 0


class SingleInstanceGuard:
    """基于 QLockFile + QLocalServer 的单实例守卫。

    典型用法（在 main.py 中）::

        guard = SingleInstanceGuard()
        if not guard.try_acquire():
            guard.notify_existing()   # 唤醒已有实例
            sys.exit(0)               # 静默退出，退出码 0
        ...
        guard.set_activate_callback(window.bring_to_front)

    Attributes:
        _lock_path: 锁文件路径。
        _server_name: 本地服务名。
        _activated: 是否已持有锁（成为唯一实例）。
    """

    def __init__(
        self,
        lock_path: Optional[Path] = None,
        server_name: str = DEFAULT_SERVER_NAME,
        stale_lock_ms: int = _STALE_LOCK_MS,
    ) -> None:
        """初始化守卫（不做任何加锁动作，加锁见 :meth:`try_acquire`）。

        Args:
            lock_path: 锁文件路径，默认 ``~/.wallpace/wallpace.lock``。
            server_name: QLocalServer 名称，用于唤醒。
            stale_lock_ms: QLockFile 陈旧锁判定时间（毫秒）。
        """
        self._lock_path: Path = Path(lock_path) if lock_path is not None \
            else DEFAULT_LOCK_PATH
        self._server_name: str = server_name
        self._stale_lock_ms: int = stale_lock_ms

        self._lock: Optional[QLockFile] = None
        self._server: Optional[QLocalServer] = None
        self._activated: bool = False
        self._degraded: bool = False
        self._activate_callback: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    @property
    def is_primary(self) -> bool:
        """是否已成功成为唯一（首）实例。"""
        return self._activated

    @property
    def is_degraded(self) -> bool:
        """是否因内部异常而降级（未加锁但允许启动）。"""
        return self._degraded

    @property
    def server_name(self) -> str:
        """本地服务名（测试/调试用）。"""
        return self._server_name

    def set_activate_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """注册"第二实例请求唤醒"时的回调（通常是主窗口的 bring_to_front）。

        Args:
            callback: 无参可调用对象；传 None 表示清除。
        """
        self._activate_callback = callback

    # ------------------------------------------------------------------ #
    # 获取 / 释放
    # ------------------------------------------------------------------ #
    def try_acquire(self) -> bool:
        """尝试成为唯一实例。

        Returns:
            True  表示"可以启动"：要么成功持有锁，要么因内部异常降级；
            False 表示"已有实例在跑"，调用方应 :meth:`notify_existing` 后退出。

        注意：任何内部异常（构造锁失败、tryLock 抛错等）都返回 True，
        保证用户永远不会被锁机制挡在门外。
        """
        try:
            lock = self._make_lock_file()
            self._lock = lock
            if lock.tryLock(0):
                self._activated = True
                self._start_server()
                return True
            # tryLock 返回 False = 锁被另一个活着的进程持有。
            logger.info("检测到已有 Wallpace 实例在运行（锁: %s）", self._lock_path)
            return False
        except Exception:  # noqa: BLE001 —— 降级护栏：绝不让锁问题阻断启动
            logger.exception("单实例检测失败，降级为允许启动（本次不做单实例保护）")
            self._degraded = True
            return True

    def notify_existing(self) -> bool:
        """作为第二实例，向已有实例发送 ``"activate"`` 请求其激活窗口。

        纯"尽力而为"：任何失败都只记日志并返回 False，绝不抛异常——
        第二实例无论如何都要能干净退出。

        Returns:
            True 表示消息已成功写出；False 表示连接/写入失败。
        """
        if not LOCAL_SOCKET_AVAILABLE or QLocalSocket is None:
            logger.info("本环境无 QtNetwork，跳过唤醒已有实例")
            return False
        try:
            sock = QLocalSocket()
            sock.connectToServer(self._server_name)
            if not sock.waitForConnected(_CONNECT_TIMEOUT_MS):
                logger.warning(
                    "无法连接已有实例(服务名=%s): %s",
                    self._server_name, sock.errorString(),
                )
                return False
            sock.write(ACTIVATE_MESSAGE)
            sock.flush()
            # 实测：无事件循环驱动时 ``waitForBytesWritten`` 可能返回 False，
            # 且若函数返回后 socket 立刻被 GC，Qt 内部缓冲尚未交给底层管道，
            # 消息会**丢失**（实测同进程"丢 / 不丢"只差"是否保活 socket"）。
            # 因此这里用 processEvents 驱动直到写完，并让 socket 存活到写完为止。
            deadline = time.time() + _WRITE_TIMEOUT_MS / 1000.0
            while sock.bytesToWrite() > 0 and time.time() < deadline:
                QCoreApplication.processEvents()
                sock.flush()
                time.sleep(0.005)
            sock.disconnectFromServer()
            logger.info("已请求已有实例激活主窗口")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("通知已有实例失败（忽略，直接退出）")
            return False

    def release(self) -> None:
        """释放本地服务与锁。退出程序或测试清理时调用，幂等且不抛异常。"""
        try:
            if self._server is not None:
                self._server.close()
                if QLocalServer is not None:
                    QLocalServer.removeServer(self._server_name)
                self._server = None
        except Exception:  # noqa: BLE001
            logger.exception("关闭单实例本地服务失败")
        try:
            if self._lock is not None and self._activated:
                self._lock.unlock()
        except Exception:  # noqa: BLE001
            logger.exception("释放单实例锁失败")
        finally:
            self._activated = False

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _make_lock_file(self) -> QLockFile:
        """构造 QLockFile（确保父目录存在）。

        Returns:
            已配置陈旧时间的 QLockFile 实例。

        Raises:
            由 pathlib / QLockFile 在无法创建时抛出的异常，由 try_acquire
            统一兜底降级。
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self._lock_path))
        lock.setStaleLockTime(self._stale_lock_ms)
        return lock

    def _start_server(self) -> None:
        """尝试监听本地套接字以接收唤醒消息。

        失败不影响持有锁的事实，只是"唤醒功能不可用"，仅记日志。
        """
        if not LOCAL_SOCKET_AVAILABLE or QLocalServer is None:
            logger.info("本环境无 QtNetwork，跳过启动单实例本地服务")
            return
        try:
            QLocalServer.removeServer(self._server_name)
            server = QLocalServer()
            if not server.listen(self._server_name):
                logger.warning(
                    "单实例本地服务监听失败(服务名=%s): %s，唤醒功能不可用",
                    self._server_name, server.errorString(),
                )
                return
            server.newConnection.connect(self._on_new_connection)
            self._server = server
        except Exception:  # noqa: BLE001
            logger.exception("启动单实例本地服务失败，唤醒功能不可用")

    def _on_new_connection(self) -> None:
        """处理新连接：取出连接并挂上读取回调。"""
        server = self._server
        if server is None:
            return
        try:
            while server.hasPendingConnections():
                sock = server.nextPendingConnection()
                if sock is None:
                    break
                sock.readyRead.connect(lambda s=sock: self._on_socket_ready(s))
                if sock.bytesAvailable():
                    self._on_socket_ready(sock)
        except Exception:  # noqa: BLE001
            logger.exception("处理单实例连接失败")

    def _on_socket_ready(self, sock: QLocalSocket) -> None:
        """读取到达的消息，匹配 ``"activate"`` 时触发回调。"""
        try:
            data = bytes(sock.readAll()).strip()
            if data and data == ACTIVATE_MESSAGE:
                self._fire_activate()
        except RuntimeError:
            # 实测：socket 的 C++ 对象可能在 disconnected/清理后被删除，
            # 此时再触碰会抛 "Internal C++ object already deleted"，忽略即可。
            return
        except Exception:  # noqa: BLE001
            logger.exception("读取单实例消息失败")
            return
        finally:
            try:
                sock.deleteLater()
            except RuntimeError:
                pass

    def _fire_activate(self) -> None:
        """安全地调用唤醒回调（回调异常不得传播）。"""
        callback = self._activate_callback
        if callback is None:
            logger.debug("收到 activate 但未注册回调，忽略")
            return
        try:
            callback()
            logger.info("已响应 activate，主窗口已尝试恢复前台")
        except Exception:  # noqa: BLE001
            logger.exception("响应 activate 回调失败")
