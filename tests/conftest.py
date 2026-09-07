"""tests/conftest.py — 共享 fixtures。"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QImageWriter

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for env_var in ("PYTHONPATH", "TMPDIR", "TEMP", "TMP"):
    if env_var == "PYTHONPATH":
        os.environ.setdefault(env_var, str(ROOT))
    else:
        os.environ.setdefault(env_var, str(Path(tempfile.gettempdir())))


def _compat_qimagewriter_write(image: QImage, file_name, format_name=None):
    """兼容旧测试对 QImageWriter.write 的调用方式。"""
    fmt = "PNG"
    if format_name:
        fmt = str(format_name).upper()
        if fmt in {"JPG", "JPEG"}:
            fmt = "JPG"
    path = Path(file_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return image.save(str(path), fmt)


QImageWriter.write = _compat_qimagewriter_write

# 注意：此处曾给 QMetaObject.Connection 打过一个返回 None 的 no-op
# `disconnect()` 补丁。它让 110 个用例在生产环境行为不同的前提下全部通过，
# 掩盖了 `_GalleryThumbWidget.cleanup()` 在生产环境抛 AttributeError 的 P0 缺陷。
# 现在 image_loader 已改为"按 path 订阅 + cancel() 退订"，代码中不再调用
# Connection.disconnect()，该补丁已移除，测试恢复反映真实行为。


@pytest.fixture()
def tmp_config_dir(tmp_path: Path, monkeypatch):
    """提供一个隔离的临时配置目录。

    默认配置位置已改为 %APPDATA%/.wallpace/config.json（与 CWD 无关），
    因此必须把 DEFAULT_CONFIG_PATH 重定向到临时目录，否则用例会读写真实用户
    配置；同时禁用旧配置路径检查，避免测试从项目根/真实 CWD 迁移真实数据。

    注意：测试可能以 `src.core.settings` 或 `core.settings` 两种 import 风格
    加载同一文件，二者在 sys.modules 中是不同模块对象，需一并重定向。
    """
    import os
    import sys

    for _mod_name in ("src.core.settings", "core.settings"):
        _mod = sys.modules.get(_mod_name)
        if _mod is None:
            continue
        monkeypatch.setattr(_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(_mod, "LEGACY_CONFIG_PATHS", [])

    old_cwd = Path.cwd()
    try:
        os.chdir(str(tmp_path))
        yield tmp_path
    finally:
        os.chdir(str(old_cwd))


@pytest.fixture()
def sample_image_files(tmp_path: Path):
    """创建一个带图片文件的临时目录，供扫描测试使用。"""
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    for name in ("a.jpg", "b.png", "c.webp", "d.bmp"):
        (img_dir / name).write_text("fake")
    sub = img_dir / "sub"
    sub.mkdir()
    (sub / "e.jpg").write_text("fake")
    (sub / "readme.txt").write_text("ignore")
    return str(img_dir)
