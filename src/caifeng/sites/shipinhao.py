"""微信视频号：只认链接、提示不支持。

分享链接打开的网页预览没有视频地址，只能在微信里播放且视频流加密（2026-10 实测），下载得绕过限制，所以不做。
"""

from __future__ import annotations

NAME = "视频号"


def match(url: str) -> bool:
    return "weixin.qq.com/sph" in url or "channels.weixin.qq.com" in url


def current(page) -> str | None:
    return None


def fetch(page, url: str):
    raise RuntimeError("视频号的视频只能在微信里播放，网页上拿不到，采风不支持下载")
