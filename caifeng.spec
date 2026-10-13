# -*- mode: python ; coding: utf-8 -*-
# 一次分析出单文件便携版和给安装包用的文件夹版（启动快）。用 scripts/build.py 跑，它会接着打安装包
from PyInstaller.utils.hooks import copy_metadata

datas = [
    ("src/caifeng/web", "caifeng/web"),
    ("src/caifeng/assets/icon.ico", "caifeng/assets"),
]
datas += copy_metadata("caifeng")  # 版本号

a = Analysis(
    ["src/caifeng/__main__.py"],
    pathex=["src"],
    datas=datas,
    excludes=["pytest", "ruff", "PIL", "tkinter", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
common = dict(console=False, upx=False, icon="src/caifeng/assets/icon.ico")

# 单文件
EXE(pyz, a.scripts, a.binaries, a.datas, [], name="caifeng-portable", **common)

# 文件夹（exe 名字不能和上面相同，否则中间文件会互相覆盖）
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="caifeng", **common)
COLLECT(app, a.binaries, a.datas, upx=False, name="caifeng")
