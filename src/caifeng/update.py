"""检查更新：查 GitHub Releases 的最新版本，只提示、不自动下载安装。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from caifeng import __version__

REPO = "mrxxaxx/caifeng"
PAGE = f"https://github.com/{REPO}"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"  # 只算正式版，不含预发布


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


def check(current: str = __version__, timeout: float = 8) -> dict:
    """返回 {ok, newer, latest, url, message}。ok=False 是没查成（网络等），界面不用打扰用户。"""
    req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "caifeng"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # 还没发过版本
            return {"ok": True, "newer": False, "latest": "", "url": PAGE, "message": "已经是最新版本"}
        return {"ok": False, "newer": False, "latest": "", "url": PAGE, "message": f"检查更新失败（HTTP {e.code}）"}
    except (OSError, ValueError) as e:
        return {"ok": False, "newer": False, "latest": "", "url": PAGE, "message": f"检查更新失败：{e}"}
    latest = str(data.get("tag_name") or "").lstrip("v")
    url = str(data.get("html_url") or f"{PAGE}/releases")
    if newer(latest, current):
        return {"ok": True, "newer": True, "latest": latest, "url": url, "message": f"有新版本 v{latest}"}
    return {"ok": True, "newer": False, "latest": latest, "url": url, "message": "已经是最新版本"}
