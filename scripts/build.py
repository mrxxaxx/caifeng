"""打包：uv run python scripts/build.py，在 dist/ 出便携版 exe 和安装包（安装包需要 Inno Setup）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import tomllib
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_iscc() -> Path | None:
    """Inno Setup 的命令行编译器：先看 PATH，再看注册表里的安装位置。"""
    if found := shutil.which("ISCC"):
        return Path(found)
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, base in (
        (winreg.HKEY_CURRENT_USER, uninstall),
        (winreg.HKEY_LOCAL_MACHINE, uninstall),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        for version in (7, 6):
            try:
                with winreg.OpenKey(hive, rf"{base}\Inno Setup {version}_is1") as key:
                    exe = Path(winreg.QueryValueEx(key, "InstallLocation")[0]) / "ISCC.exe"
            except OSError:
                continue
            if exe.exists():
                return exe
    return None


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    dist = ROOT / "dist"
    portable = dist / f"caifeng-v{version}-portable.exe"
    # 正在运行的旧版会锁住 dist 里的文件
    if subprocess.run(["taskkill", "/F", "/IM", "caifeng*"], capture_output=True).returncode == 0:
        time.sleep(1.5)  # 等进程退出、放开文件

    print(f"== PyInstaller（{version}）")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "caifeng.spec"], cwd=ROOT, check=True)
    (dist / "caifeng-portable.exe").replace(portable)

    iscc = find_iscc()
    if not iscc:
        print("没找到 Inno Setup（ISCC.exe），只打了便携版。安装：winget install JRSoftware.InnoSetup")
        return 1
    print("== Inno Setup")
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={version}", "installer/caifeng.iss"], cwd=ROOT, check=True)

    for f in (portable, dist / f"caifeng-v{version}-setup.exe"):
        print(f"{f}  {f.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
