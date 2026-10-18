# -*- mode: python ; coding: utf-8 -*-
# G12：onedir + 去 UPX（绿色版产物 dist/wallpace/，分发打 zip）。
# - onedir：常驻托盘工具不适合 onefile 每次往 temp 解压；启动快、杀软误报低。
# - 去 UPX：UPX 压缩壳是杀软高频误报源；本场景（发给朋友）不做代码签名，
#   SmartScreen「未知发布者」可接受，用去 UPX 换误报率。
# - 路径自定位：PyInstaller 执行 spec 时 `__file__` 未定义，用 spec 执行后的
#   SPECPATH（spec 所在目录）解析仓库根，避免「必须从仓库根目录启动」的
#   隐式假设；固定 DISTPATH/WORKPATH 在仓库根（否则产物会落在
#   packaging/dist，build.bat 会误报"打包失败"）。
import os

# PyInstaller 注入的 SPECPATH 即 spec 所在目录（packaging/），直接用它定位仓库根。
_spec_dir = os.path.abspath(SPECPATH)
DISTPATH = os.path.abspath(os.path.join(_spec_dir, '..', 'dist'))
WORKPATH = os.path.abspath(os.path.join(_spec_dir, '..', 'build'))


a = Analysis(
    [os.path.abspath(os.path.join(_spec_dir, '..', 'src', 'main.py'))],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='wallpace',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='wallpace',
)
