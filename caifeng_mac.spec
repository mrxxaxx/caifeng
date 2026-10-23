# -*- mode: python ; coding: utf-8 -*-
# macOS 打包：出 采风.app（文件夹应用），再由 scripts/build_mac.py 打成 .dmg。
# playwright 的 node 驱动由它自带的 PyInstaller hook 自动打包，这里不用管。
import os
import tomllib

from PyInstaller.utils.hooks import copy_metadata

version = tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]
icon = "src/caifeng/assets/icon.icns"
if not os.path.exists(icon):
    icon = None  # 没有 icns 就用默认图标，不影响打包

datas = [("src/caifeng/web", "caifeng/web")]
datas += copy_metadata("caifeng")  # 版本号

a = Analysis(
    ["src/caifeng/__main__.py"],
    pathex=["src"],
    datas=datas,
    excludes=["pytest", "ruff", "PIL", "tkinter", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="采风", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="采风")
app = BUNDLE(
    coll,
    name="采风.app",
    icon=icon,
    bundle_identifier="com.xxa.caifeng",
    info_plist={
        "CFBundleName": "采风",
        "CFBundleDisplayName": "采风",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
