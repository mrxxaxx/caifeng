"""macOS 打包：uv run python scripts/build_mac.py

在 dist/ 出一个文件：
- caifeng-v版本号-macos-arm64.dmg  拖进「应用程序」就能装

只在 macOS 上跑（Apple 芯片）。没签名，用户首次打开要右键→打开，或 `xattr -dr com.apple.quarantine 采风.app`。
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "caifeng" / "assets"

# iconutil 需要的标准 iconset：文件名 → 像素尺寸
ICONSET = {
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}  # fmt: skip


def make_icns(ico: Path, icns: Path) -> bool:
    """用 sips + iconutil 从 .ico 生成 .icns；哪一步失败就返回 False，用默认图标。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.png"
            if subprocess.run(["sips", "-s", "format", "png", str(ico), "--out", str(base)]).returncode != 0:
                return False
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for name, size in ICONSET.items():
                subprocess.run(["sips", "-z", str(size), str(size), str(base), "--out", str(iconset / name)],
                               check=True)  # fmt: skip
            return subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)]).returncode == 0
    except (OSError, subprocess.CalledProcessError):
        return False


def make_dmg(app: Path, dmg: Path) -> None:
    """把 .app 和「应用程序」快捷方式放进临时文件夹，压成 dmg（拖进去即装）。"""
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        subprocess.run(["cp", "-R", str(app), str(stage / app.name)], check=True)
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(
            ["hdiutil", "create", "-volname", "采风", "-srcfolder", str(stage),
             "-ov", "-format", "UDZO", str(dmg)],
            check=True,
        )  # fmt: skip


def main() -> int:
    if sys.platform != "darwin":
        print("这个脚本只在 macOS 上跑")
        return 1
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    dist = ROOT / "dist"

    icns = ASSETS / "icon.icns"
    if make_icns(ASSETS / "icon.ico", icns):
        print(f"== 图标 {icns.name}")
    else:
        print("== 没能生成 icns，用默认图标")

    print(f"== PyInstaller（{version}）")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "caifeng_mac.spec"], cwd=ROOT, check=True)
    app = dist / "采风.app"
    # 兜底写入版本号（spec 里已写，这里确保万无一失）
    plist = app / "Contents" / "Info.plist"
    if plist.exists():
        info = plistlib.loads(plist.read_bytes())
        info["CFBundleShortVersionString"] = version
        plist.write_bytes(plistlib.dumps(info))

    print("== 打 dmg")
    dmg = dist / f"caifeng-v{version}-macos-arm64.dmg"
    make_dmg(app, dmg)
    print(f"{dmg}  {dmg.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
