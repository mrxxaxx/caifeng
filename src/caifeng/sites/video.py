"""短视频：从粘贴的内容里认出各平台的链接，统一下载和命名。

每个平台模块提供：NAME（也是子文件夹名）、match(url)、fetch(page, url) -> Item 或 [Item]（豆包一段对话可能有好几个）、
current(page)（「取当前页」用，没有返回 None）。
"""

from __future__ import annotations

import contextlib
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from caifeng.sites import bilibili, doubao, douyin, kuaishou, shipinhao, xiaohongshu
from caifeng.sites.common import Item, fetch_file, safe_name

PLATFORMS = (douyin, kuaishou, xiaohongshu, bilibili, doubao, shipinhao)  # 视频号只认链接、提示不支持
INIT_SCRIPTS = tuple(p.INIT_SCRIPT for p in PLATFORMS if hasattr(p, "INIT_SCRIPT"))  # 注入采风打开的浏览器
NAME = "短视频"
HOME = douyin.HOME  # 「在浏览器打开」没指定平台时打开抖音
LOGIN_REQUIRED = False  # 都能用游客身份下；登录过的网站（比如 B 站）能下更高清的
# 浏览器和下载都不走系统代理：快手对境外 IP 不给作品数据，B 站的 PCDN 节点走代理常超时（2026-10 实测）
DIRECT = True

URL = re.compile(r"https?://[^\s，。、！？“”‘’()（）<>【】]+")

Log = Callable[[str], None]
Stop = Callable[[], bool]


def is_logged_in(page) -> bool:
    return False  # 不用登录，界面上不显示登录状态


def platform_of(url: str):
    return next((p for p in PLATFORMS if p.match(url)), None)


def links_in(text: str) -> list[str]:
    """粘贴内容（整段分享口令也行）里认得的链接，去重保持顺序。"""
    out: list[str] = []
    for u in URL.findall(text or ""):
        u = u.rstrip(".,;:!?'\"/") + ("/" if u.endswith("/") else "")
        if platform_of(u) and u not in out:
            out.append(u)
    return out


def current(page) -> str | None:
    p = platform_of(page.url)
    return p.current(page) if p else None


def file_stem(item: Item) -> str:
    """文件名：发布日期 + 标题（太长截断）。"""
    day = time.strftime("%Y-%m-%d", time.localtime(item.created or time.time()))
    title = safe_name(item.title)[:40].rstrip(". ") or item.id
    return f"{day} {title}"


def free_name(path: Path) -> Path:
    """和浏览器一样：重名就叫「名字 (1).mp4」「名字 (2).mp4」……文件夹也一样。"""
    if not path.exists():
        return path
    # 文件夹名里的点不是扩展名（「v1.2 教程」）
    stem, suffix = (path.stem, path.suffix) if path.suffix.lower() in (".mp4", ".jpg") else (path.name, "")
    n = 1
    while (p := path.with_name(f"{stem} ({n}){suffix}")).exists():
        n += 1
    return p


def _fetch_any(urls: list[str], dst: Path, should_stop, transfer, headers) -> bool:
    """按顺序试几个地址（B 站每路都给了备用地址），连不上就换下一个；都不行报最后一个错。"""
    error: OSError | None = None
    for u in filter(None, urls):
        try:
            return fetch_file(u, dst, should_stop, transfer, headers=headers, direct=DIRECT)
        except OSError as e:  # 连不上、超时、403
            error = e
    raise error or RuntimeError("没有可下载的地址")


def resolve(page, url: str, nomark: bool = False) -> list[Item]:
    """打开链接拿到作品（豆包一段对话里可能有好几个）。nomark：豆包下不带 AI 标识的版本。"""
    platform = platform_of(url)
    if platform is None:
        raise RuntimeError("不认识这个链接")
    got = platform.fetch(page, url, nomark=nomark) if platform is doubao else platform.fetch(page, url)
    return got if isinstance(got, list) else [got]


def save(
    item: Item,
    out_root: Path,
    *,
    log: Log = print,
    transfer: Callable[[int, int], None] | None = None,
    should_stop: Stop | None = None,
) -> dict:
    """下载一个作品到 out_root/平台名/：视频存成一个 .mp4；图文存成一个文件夹，里面是编号的图片和 文案.txt。"""
    out_dir = Path(out_root) / item.platform
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem(item)
    log(f"{item.platform}：{item.title or item.id}" + (f"（{item.author}）" if item.author else ""))
    if item.note:
        log(item.note)

    if item.images:
        folder = free_name(out_dir / stem)
        folder.mkdir(parents=True)
        ok = False
        try:
            for i, u in enumerate(item.images, 1):
                if not fetch_file(
                    u, folder / f"{i:02d}.jpg", should_stop, transfer, headers=item.headers, direct=DIRECT
                ):
                    return {"stopped": True, "title": stem}
            if item.text:
                (folder / "文案.txt").write_text(item.text + "\n", encoding="utf-8")
            ok = True
        finally:  # 停止或失败不留半截文件夹
            if not ok:
                shutil.rmtree(folder, ignore_errors=True)
        log(f"图文 {len(item.images)} 张" + ("，文案已保存" if item.text else ""))
        return {"stopped": False, "title": stem, "file": str(folder), "out_dir": str(out_dir)}

    if not item.video:
        raise RuntimeError("这个作品没有可下载的视频地址")
    dst = free_name(out_dir / f"{stem}.mp4")
    if not item.audio:
        if not _fetch_any([item.video, *item.video_alt], dst, should_stop, transfer, item.headers):
            return {"stopped": True, "title": stem}
    else:  # 画面和声音分开的（B 站）：各下一个，再合成一个文件
        from caifeng.media import mux

        parts = [dst.with_name(dst.stem + ".画面.m4s"), dst.with_name(dst.stem + ".声音.m4s")]
        sources = ([item.video, *item.video_alt], [item.audio, *item.audio_alt])
        try:
            for urls, part in zip(sources, parts, strict=True):
                if not _fetch_any(urls, part, should_stop, transfer, item.headers):
                    return {"stopped": True, "title": stem}
            log("合并画面和声音")
            mux(parts[0], parts[1], dst)
        finally:  # 成功、失败、停止都不留中间文件
            for part in parts:
                part.unlink(missing_ok=True)
                part.with_name(part.name + ".part").unlink(missing_ok=True)
    if item.tags:
        _write_tags(item, dst, log)
    log(f"视频 {dst.stat().st_size / 1048576:.1f} MB" + (f"，{item.quality}" if item.quality else ""))
    return {"stopped": False, "title": stem, "file": str(dst), "out_dir": str(out_dir)}


def _write_tags(item: Item, dst: Path, log: Log) -> None:
    """把标签写进下好的文件（豆包无水印版本：补回 AIGC 隐式标识，标明是 AI 生成）。"""
    from caifeng import media

    tags = dict(item.tags)
    if item.tags_from:  # 带标识的那一路文件里有平台写的原值（含这条内容的编号），读得到就用它，读不到用默认值
        with contextlib.suppress(Exception):
            tags.update(media.read_tags(item.tags_from, list(tags), user_agent=item.headers.get("user-agent", "")))
    media.write_tags(dst, tags)
    log("已写入 AI 生成的隐式标识")
