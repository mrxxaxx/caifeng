"""各站点共用：文件名、下载文件、短视频作品的统一结构。"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
Stop = Callable[[], bool]


def safe_name(name: str) -> str:
    # Windows 会悄悄去掉名字末尾的点和空格，建好的文件夹就和程序以为的对不上
    return UNSAFE.sub("_", name or "").strip().rstrip(". ")


def fetch_file(
    url: str,
    dst: Path,
    should_stop: Stop | None = None,
    on_bytes: Callable[[int, int], None] | None = None,
    headers: dict[str, str] | None = None,
    direct: bool = False,
) -> bool:
    """在浏览器外下载到 dst（先写 .part，完整了再改名），才能报进度；中途停止返回 False。

    地址自带签名不需要 cookie，个别站点要的 Referer 由 headers 传入。on_bytes 的总大小未知时为 0；direct 不走系统代理。
    """
    part = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=30) as r, open(part, "wb") as f:
            size = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(256 << 10):
                if should_stop and should_stop():
                    break
                f.write(chunk)
                got += len(chunk)
                if on_bytes:
                    on_bytes(got, size)
            else:
                if size and got != size:  # 连接中途断了：不能当成下完了
                    raise OSError(f"下载不完整（{got} / {size} 字节）")
                f.close()
                part.replace(dst)
                return True
    except BaseException:
        part.unlink(missing_ok=True)  # 超时、断线：不留半截文件
        raise
    part.unlink(missing_ok=True)
    return False


@dataclass
class Item:
    """一个作品：要么是视频（video，B 站另有单独的声音 audio，下完合并），要么是一组图（images）。"""

    platform: str  # 平台中文名
    id: str
    title: str
    url: str  # 作品页地址
    author: str = ""
    created: float = 0  # 发布时间（时间戳）
    duration: float = 0  # 秒，挑选列表里显示
    cover: str = ""  # 封面图，挑选列表里显示
    video: str = ""
    audio: str = ""
    video_alt: list[str] = field(default_factory=list)  # 备用地址，前一个连不上时换着试
    audio_alt: list[str] = field(default_factory=list)
    quality: str = ""  # 「1080P」
    images: list[str] = field(default_factory=list)
    thumbs: list[str] = field(default_factory=list)  # 图文每张图的小图，挑图时显示（没有就用 images）
    text: str = ""  # 图文的正文，和图片一起存成 文案.txt
    headers: dict[str, str] = field(default_factory=dict)  # 下载文件时要带的请求头
    note: str = ""  # 要提醒用户的话，比如「没登录，最高 480P」
    tags: dict[str, str] = field(default_factory=dict)  # 下载后写进文件元数据的标签（豆包无水印版本的 AIGC 隐式标识）
    tags_from: str = ""  # 先从这个地址的文件里读同名标签，读到就用它的值（只读文件头）
