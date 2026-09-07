"""tests/test_image_loader.py — 异步图片解码器测试。

测试 image_loader 模块的线程池管理和异步解码功能。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.app import image_loader
from PySide6.QtCore import QSize, QCoreApplication
from PySide6.QtGui import QImage


class TestImageLoaderImport:
    """测试模块导入和基本结构。"""

    def test_load_async_exists(self):
        assert hasattr(image_loader, "load_async")
        assert callable(image_loader.load_async)

    def test_connect_ready_exists(self):
        assert hasattr(image_loader, "connect_ready")
        assert callable(image_loader.connect_ready)

    def test_pool_is_singleton(self):
        pool1 = image_loader._get_pool()
        pool2 = image_loader._get_pool()
        assert pool1 is pool2


class TestImageLoaderPool:
    """测试线程池配置。"""

    def test_max_thread_count(self):
        pool = image_loader._get_pool()
        assert pool.maxThreadCount() == 4

    def test_pool_is_global_instance(self):
        from PySide6.QtCore import QThreadPool
        pool = image_loader._get_pool()
        assert pool is QThreadPool.globalInstance()


class TestPerPathSubscription:
    """按 path 分发的订阅表（P0-1 根治 + P1-4 O(N²) 扇出的解法）。"""

    @pytest.fixture(autouse=True)
    def app(self):
        """确保有 QApplication 实例，且必须在 offscreen 模式。"""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            QApplication([])
        yield

    @staticmethod
    def _wait(predicate, timeout: float = 5.0) -> bool:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            QCoreApplication.processEvents()
            time.sleep(0.02)
        return predicate()

    def test_on_ready_receives_only_its_own_path(self, tmp_path):
        """N 个订阅者各收各的，回调次数是 O(N) 而不是 O(N²)。"""
        from PySide6.QtGui import QImageWriter
        img = QImage(40, 40, QImage.Format_RGB32)
        img.fill(0x0000FF)
        path = tmp_path / "per_path.png"
        QImageWriter.write(img, str(path), "PNG")

        hits = []
        image_loader.load_async(
            str(path), QSize(40, 40), lambda p, i: hits.append(p)
        )
        assert self._wait(lambda: len(hits) == 1)
        assert Path(hits[0]).name == "per_path.png"

    def test_cancel_prevents_callback(self, tmp_path):
        """cancel() 退订后不得再有回调（替代失效的 Connection.disconnect）。"""
        from PySide6.QtGui import QImageWriter
        img = QImage(60, 60, QImage.Format_RGB32)
        img.fill(0x00FFFF)
        path = tmp_path / "cancelled.png"
        QImageWriter.write(img, str(path), "PNG")

        calls = []

        def on_ready(p, i):
            calls.append(p)

        image_loader.load_async(str(path), QSize(60, 60), on_ready)
        image_loader.cancel(str(path), on_ready)

        # 给足时间让后台解码完成并尝试投递
        import time
        QCoreApplication.processEvents()
        time.sleep(0.6)
        QCoreApplication.processEvents()
        assert calls == []

    def test_cancel_unknown_callback_is_noop(self):
        """退订未登记的回调不得抛异常（幂等）。"""

        def on_ready(p, i):
            pass

        image_loader.cancel("/not/registered.png", on_ready)

    def test_multiple_subscribers_same_path(self, tmp_path):
        """同一个 path 的多个订阅者都应收到结果。"""
        from PySide6.QtGui import QImageWriter
        img = QImage(30, 30, QImage.Format_RGB32)
        img.fill(0xFF00FF)
        path = tmp_path / "shared.png"
        QImageWriter.write(img, str(path), "PNG")

        a, b = [], []
        image_loader.load_async(str(path), QSize(30, 30), lambda p, i: a.append(p))
        image_loader.load_async(str(path), QSize(30, 30), lambda p, i: b.append(p))

        assert self._wait(lambda: a and b)
        assert len(a) == 1 and len(b) == 1

    def test_failed_decode_discards_subscription(self, tmp_path):
        """解码失败时必须清理订阅表，避免条目无限堆积。"""
        missing = str(tmp_path / "missing.png")
        image_loader.load_async(missing, QSize(10, 10), lambda p, i: None)
        assert self._wait(lambda: image_loader.pending_count() == 0)
        assert image_loader.pending_count() == 0


class TestBroadcastSubscription:
    """connect_ready() 返回的订阅对象必须提供真实可用的 disconnect()。

    历史问题：PySide6 的 QMetaObject.Connection 在本机没有 disconnect()，
    测试用 no-op 补丁掩盖了这一点，导致生产环境 AttributeError（P0-1）。
    现在 connect_ready 返回自定义订阅对象，disconnect 真实生效。
    """

    @pytest.fixture(autouse=True)
    def app(self):
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            QApplication([])
        yield

    def test_disconnect_is_real_and_idempotent(self, tmp_path):
        from PySide6.QtGui import QImageWriter
        img = QImage(20, 20, QImage.Format_RGB32)
        img.fill(0x123456)
        path = tmp_path / "broadcast.png"
        QImageWriter.write(img, str(path), "PNG")

        received = []
        sub = image_loader.connect_ready(lambda p, i: received.append(p))
        try:
            image_loader.load_async(str(path), QSize(20, 20))
            import time
            deadline = time.time() + 5
            while not received and time.time() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.02)
            assert len(received) == 1

            sub.disconnect()
            assert sub.is_active is False
            received.clear()

            image_loader.load_async(str(path), QSize(20, 20))
            QCoreApplication.processEvents()
            time.sleep(0.5)
            QCoreApplication.processEvents()
            assert received == []

            sub.disconnect()  # 幂等，重复调用不抛
        finally:
            sub.disconnect()
    """测试实际图片解码（需要 QApplication 实例）。"""

    @pytest.fixture(autouse=True)
    def app(self):
        """确保有 QApplication 实例，且必须在 offscreen 模式。"""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            QApplication([])
        yield

    def test_decode_small_image(self, tmp_path):
        """测试解码小尺寸图片。"""
        from PySide6.QtGui import QImageWriter
        img = QImage(50, 50, QImage.Format_RGB32)
        img.fill(0xFF0000)  # 红色
        path = tmp_path / "red.png"
        QImageWriter.write(img, str(path), "PNG")

        results = []
        conn = image_loader.connect_ready(lambda p, img: results.append((p, img)))
        try:
            image_loader.load_async(str(path), QSize(50, 50))
            # 等待解码完成（最多 5 秒）
            for _ in range(50):
                if results:
                    break
                QCoreApplication.processEvents()
                import time; time.sleep(0.1)
        finally:
            conn.disconnect()

        assert len(results) == 1
        path_received, img_received = results[0]
        assert Path(path_received).name == "red.png"
        assert img_received.width() == 50
        assert img_received.height() == 50

    def test_decode_nonexistent_image(self, tmp_path):
        """测试解码不存在的图片不崩溃。"""
        results = []
        conn = image_loader.connect_ready(lambda p, img: results.append((p, img)))
        try:
            image_loader.load_async(str(tmp_path / "nonexistent.png"), QSize(50, 50))
            import time; time.sleep(0.5)
            QCoreApplication.processEvents()
        finally:
            conn.disconnect()

        # 不存在的图片不应该产生结果
        assert len(results) == 0

    def test_decode_jpeg(self, tmp_path):
        """测试解码 JPEG 图片。"""
        from PySide6.QtGui import QImageWriter
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(0x00FF00)  # 绿色
        path = tmp_path / "green.jpg"
        QImageWriter.write(img, str(path), "JPG")

        results = []
        conn = image_loader.connect_ready(lambda p, img: results.append((p, img)))
        try:
            image_loader.load_async(str(path), QSize(100, 100))
            for _ in range(50):
                if results:
                    break
                QCoreApplication.processEvents()
                import time; time.sleep(0.1)
        finally:
            conn.disconnect()

        assert len(results) == 1
        _, img_received = results[0]
        assert img_received.width() == 100
        assert img_received.height() == 100
