"""抖音（www.douyin.com）：视频或图文。

接口要 a_bogus 签名，yt-dlp 之类直接请求会 403；所以让浏览器打开作品页，截下页面自己请求的
/aweme/v1/web/aweme/detail/ 响应。视频地址自带签名，带上 Referer 即可下载。
"""

from __future__ import annotations

import re

from caifeng.sites.common import Item

NAME = "抖音"
HOST = "https://www.douyin.com"
HOME = HOST + "/"
DETAIL_API = "/aweme/v1/web/aweme/detail/"
REFERER = {"Referer": HOME}  # 视频服务器只认这个：不带返回 403

# 作品页：/video/123、/note/123、首页弹窗 ?modal_id=123
AWEME_ID = re.compile(r"(?:/(?:video|note|slides)/|[?&]modal_id=|[?&]aweme_id=)(\d{15,})")


def match(url: str) -> bool:
    return "douyin.com" in url


def aweme_id_from_url(url: str) -> str | None:
    m = AWEME_ID.search(url or "")
    return m.group(1) if m else None


# 推荐页、精选页的网址里没有作品 ID，只能从正在播放（或看得见）的 <video> 往上找 data-aweme-id
JS_PLAYING_AWEME = """
() => {
  // 推荐页：当前这条有专门的标记，视频还没开始播就有
  const active = document.querySelector('[data-e2e="feed-active-video"][data-e2e-vid]');
  if (active && /^\\d{15,}$/.test(active.getAttribute('data-e2e-vid'))) return active.getAttribute('data-e2e-vid');
  const seen = v => { const r = v.getBoundingClientRect();
    return r.width * r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  const videos = [...document.querySelectorAll('video')].filter(seen);
  const v = videos.find(v => !v.paused) || videos[0];
  for (let e = v; e; e = e.parentElement) {
    const id = e.getAttribute('data-aweme-id') || e.getAttribute('data-e2e-vid');
    if (id && /^\\d{15,}$/.test(id)) return id;
  }
  return null;
}
"""


def playing_aweme_id(page) -> str | None:
    if aweme := aweme_id_from_url(page.url):
        return aweme
    try:
        return page.evaluate(JS_PLAYING_AWEME)
    except Exception:  # noqa: BLE001  页面正在跳转、已关闭
        return None


def current(page) -> str | None:
    aweme = playing_aweme_id(page)
    return f"{HOST}/video/{aweme}" if aweme else None


def is_logged_in(page) -> bool:
    """看有没有 sessionid_ss cookie，不用打开网页。"""
    try:
        return any(c["name"] == "sessionid_ss" and c["value"] for c in page.context.cookies(HOST))
    except Exception:  # noqa: BLE001  浏览器关了
        return False


def best_video(video: dict) -> tuple[str, int]:
    """清晰度最高、同清晰度码率最高的一路：(下载地址, 大小)。"""
    options = []
    for b in video.get("bit_rate") or []:
        pa = b.get("play_addr") or {}
        if pa.get("url_list"):
            options.append((pa.get("height") or 0, b.get("bit_rate") or 0, pa))
    pa = max(options, key=lambda o: (o[0], o[1]))[2] if options else video.get("play_addr") or {}
    urls = pa.get("url_list") or []
    if not urls:
        raise RuntimeError("这个作品没有可下载的视频地址")
    return urls[0], pa.get("data_size") or 0


def image_urls(aweme: dict) -> list[str]:
    return [(img.get("url_list") or [""])[0] for img in aweme.get("images") or [] if img.get("url_list")]


def title_of(aweme: dict) -> str:
    title = aweme.get("item_title") or re.sub(r"#\S+", "", aweme.get("desc") or "")
    return re.sub(r"\s+", " ", title).strip()


def fetch_aweme(page, url: str, timeout: float = 30) -> dict:
    """打开作品页（短链接会自动跳转），截下页面自己请求的作品详情。"""
    aweme_id = aweme_id_from_url(url)
    target = f"{HOST}/video/{aweme_id}" if aweme_id else url
    try:
        # 页面上还可能加载推荐视频的详情，知道 ID 时只认这个作品的
        def wanted(r) -> bool:
            return DETAIL_API in r.url and (not aweme_id or f"aweme_id={aweme_id}" in r.url)

        with page.expect_response(wanted, timeout=timeout * 1000) as resp:
            page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        data = resp.value.json()
    except Exception as e:  # noqa: BLE001
        if "Timeout" in type(e).__name__ or "Timeout" in str(e):
            raise RuntimeError("打开作品页后没有拿到作品信息：链接失效、作品已删除，或者需要先登录") from e
        raise
    aweme = (data or {}).get("aweme_detail")
    if not aweme:
        raise RuntimeError("没有拿到作品信息：作品可能已删除或者设为私密")
    return aweme


def fetch(page, url: str) -> Item:
    """游客就能拿到，不用登录（2026-10 实测）。"""
    aweme = fetch_aweme(page, url)
    video = "" if aweme.get("images") else best_video(aweme.get("video") or {})[0]
    return Item(
        platform=NAME,
        id=aweme["aweme_id"],
        title=title_of(aweme),
        url=f"{HOST}/video/{aweme['aweme_id']}",
        author=(aweme.get("author") or {}).get("nickname") or "",
        created=aweme.get("create_time") or 0,
        duration=((aweme.get("video") or {}).get("duration") or 0) / 1000,
        cover=(((aweme.get("video") or {}).get("cover") or {}).get("url_list") or [""])[0],
        video=video,
        images=image_urls(aweme),
        text=(aweme.get("desc") or "").strip() if aweme.get("images") else "",
        headers=REFERER,
    )
