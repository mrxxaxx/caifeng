"""检查更新、自动更新：查 GitHub Releases 的最新版本，用户点了「立即更新」才下载。

安装版下载安装包静默安装（装好自动打开），便携版下载新的 exe 放在旧的旁边，打开新的、删掉旧的。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from caifeng import __version__

REPO = "mrxxaxx/caifeng"
PAGE = f"https://github.com/{REPO}"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"  # 只算正式版，不含预发布
ASSET = f"https://api.github.com/repos/{REPO}/releases/assets/{{id}}"  # 附件下载
AFTER_UPDATE = "--after-update"  # 便携版：新版带着这个参数和旧 exe 的路径启动，等旧的退出后删掉它


def parse(v: str) -> tuple[int, ...]:
    """「v0.2.0」「0.2」→ (0, 2, 0)，认不出的返回空元组。"""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))  # 0.2 和 0.2.0 一样
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "caifeng"})


def check(current: str = __version__, timeout: float = 8) -> dict:
    """返回 {ok, newer, latest, url, message, assets}。ok=False 是没查成（网络等），界面不用打扰用户。
    assets：{"setup" / "portable": {id, name, size}}，这个版本有哪种就有哪个。"""
    empty = {"ok": True, "newer": False, "latest": "", "url": PAGE, "message": "已经是最新版本", "assets": {}}
    try:
        with urllib.request.urlopen(_request(LATEST), timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # 还没发过版本
            return empty
        return {**empty, "ok": False, "message": f"检查更新失败（HTTP {e.code}）"}
    except (OSError, ValueError) as e:
        return {**empty, "ok": False, "message": f"检查更新失败：{e}"}
    latest = str(data.get("tag_name") or "").lstrip("v")
    assets = {}
    for a in data.get("assets") or []:
        for kind in ("setup", "portable"):
            if str(a.get("name", "")).endswith(f"-{kind}.exe"):
                assets[kind] = {"id": a.get("id"), "name": a["name"], "size": a.get("size", 0)}
    result = {**empty, "latest": latest, "url": str(data.get("html_url") or f"{PAGE}/releases"), "assets": assets}
    if newer(latest, current):
        result.update(newer=True, message=f"有新版本 v{latest}")
    return result


def mode() -> str:
    """怎么换成新版：setup 安装版（exe 旁边有卸载程序）、portable 便携版；空 = 直接跑的代码，只能去发布页。"""
    if not getattr(sys, "frozen", False):
        return ""
    if sys.platform != "win32":  # macOS 版暂不支持自动更新，去发布页手动下载
        return ""
    return "setup" if (Path(sys.executable).parent / "unins000.exe").exists() else "portable"


def target(kind: str, name: str) -> Path:
    """下载到哪：安装包放临时文件夹，便携版放在旧 exe 旁边。"""
    if kind == "setup":
        return Path(tempfile.gettempdir()) / "caifeng-update" / name
    return Path(sys.executable).parent / name


def cleanup(tries: int = 60) -> bool:
    """安装版更新后：删掉下载到临时文件夹的安装包。安装程序可能还没退出、文件还占着，删不掉就隔一秒再试。"""
    import shutil
    import time

    folder = target("setup", "x").parent
    for i in range(tries):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return True
        if i + 1 < tries:
            time.sleep(1)
    return False


def download(
    asset_id: int,
    dst: Path,
    on_progress: Callable[[int, int], None],
    should_stop: Callable[[], bool],
    timeout: float = 30,
) -> bool:
    """下载附件到 dst（先写 .part，完整了再改名）。on_progress(已下载, 总大小)。中途停止返回 False，不留文件。
    网络出错抛 OSError（urllib 的错误都是它的子类）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    try:
        with (
            urllib.request.urlopen(
                _request(ASSET.format(id=asset_id), "application/octet-stream"), timeout=timeout
            ) as r,
            part.open("wb") as f,
        ):
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(256 * 1024):
                if should_stop():
                    return False
                f.write(chunk)
                done += len(chunk)
                on_progress(done, total)
        if total and done != total:
            raise OSError(f"下载不完整（{done} / {total} 字节），请重试")
        part.replace(dst)
        return True
    finally:
        part.unlink(missing_ok=True)


def launch(kind: str, file: Path) -> None:
    """打开新版（调用方随后退出）。安装版静默安装，安装包装好会自己打开采风（见 installer/caifeng.iss）。"""
    if kind == "setup":
        args = [str(file), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    else:
        args = [str(file), AFTER_UPDATE, sys.executable]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True)
