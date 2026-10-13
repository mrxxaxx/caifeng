"""哔哩哔哩（www.bilibili.com）：视频信息取自 window.__INITIAL_STATE__，播放地址在页面里带 cookie 调接口拿。

高清是画面、声音分开的 DASH，下完由 video.py 合并。不登录最高 480P，登录后 1080P 及以上。
下载要同时带 Referer 和浏览器 User-Agent，缺一个就 403（2026-10 实测）。
"""

from __future__ import annotations

import re

from caifeng.sites.common import Item

NAME = "哔哩哔哩"
HOST = "https://www.bilibili.com"
HOME = HOST + "/"
BVID = re.compile(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", re.I)
QUALITY = {127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P60", 112: "1080P 高码率", 80: "1080P",
           74: "720P60", 64: "720P", 32: "480P", 16: "360P", 6: "240P"}  # fmt: skip


def match(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


def current(page) -> str | None:
    return page.url if BVID.search(page.url) else None


def is_logged_in(page) -> bool:
    try:
        return any(c["name"] == "SESSDATA" and c["value"] for c in page.context.cookies(HOST))
    except Exception:  # noqa: BLE001  浏览器关了
        return False


JS_STATE = """
() => {
  const s = window.__INITIAL_STATE__ || {}, v = s.videoData;
  if (!v) return null;
  return { p: s.p || 1, bvid: v.bvid, cid: v.cid, title: v.title, pubdate: v.pubdate, owner: (v.owner || {}).name,
           pic: v.pic, duration: v.duration,
           pages: (v.pages || []).map(x => ({ page: x.page, part: x.part })), ua: navigator.userAgent };
}
"""

# 播放地址：fnval=4048 要 DASH（含 4K/HDR），qn=127 要最高画质；实际给多高取决于登没登录、是不是大会员
JS_PLAYURL = """
async ([bvid, cid]) => {
  const r = await fetch(`https://api.bilibili.com/x/player/playurl?bvid=${bvid}&cid=${cid}&fnval=4048&fourk=1&qn=127`,
                        { credentials: 'include' });
  return await r.json();
}
"""


def pick_dash(dash: dict) -> tuple[dict, dict | None]:
    """画面挑最清晰的（同清晰度优先 H.264，再比码率）；声音挑码率最高的。"""
    videos = dash.get("video") or []
    if not videos:
        raise RuntimeError("这个视频没有可下载的地址")
    video = max(
        videos, key=lambda v: (v.get("id") or 0, v.get("height") or 0, v.get("codecid") == 7, v.get("bandwidth") or 0)
    )
    audio = max(dash.get("audio") or [], key=lambda a: a.get("bandwidth") or 0, default=None)
    return video, audio


def by_reliability(urls: list[str]) -> list[str]:
    """B 站官方节点（upos-*.bilivideo.com）排前面；PCDN 节点（mcdn、第三方域名）常连不上，放后面兜底。"""
    return sorted(dict.fromkeys(filter(None, urls)), key=lambda u: "upos-" not in u.split("/")[2])


def fetch(page, url: str, timeout: float = 30) -> Item:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)  # 短链接 b23.tv 会自动跳到视频页
    try:
        page.wait_for_function(
            "() => window.__INITIAL_STATE__ && window.__INITIAL_STATE__.videoData", timeout=timeout * 1000
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("打开后没有拿到视频信息：链接失效、视频已删除，或者不是视频页") from e
    st = page.evaluate(JS_STATE)
    data = page.evaluate(JS_PLAYURL, [st["bvid"], st["cid"]])
    if data.get("code") != 0:
        raise RuntimeError(f"拿播放地址失败：{data.get('message') or data.get('code')}")
    play = data["data"]

    pages = st["pages"]
    multi = len(pages) > 1
    part = next((x for x in pages if x["page"] == st["p"]), None) if multi else None
    title = st["title"] + (f" P{part['page']} {part['part']}" if part else "")

    if play.get("dash"):
        v, a = pick_dash(play["dash"])
        video, audio, qn = v.get("baseUrl") or v.get("base_url"), (a or {}).get("baseUrl", ""), v.get("id")
        video, *video_alt = by_reliability([video, *(v.get("backupUrl") or [])])
        audio, *audio_alt = by_reliability([audio, *((a or {}).get("backupUrl") or [])]) or [""]
    else:  # 老视频只有一个完整的文件
        seg = (play.get("durl") or [{}])[0]
        video, audio, qn = seg.get("url", ""), "", play.get("quality")
        video_alt, audio_alt = seg.get("backup_url") or [], []
    best = max(play.get("accept_quality") or [qn or 0])
    note = ""
    if qn and best > qn and not is_logged_in(page):
        note = f"没登录 B 站，最高 {QUALITY.get(qn, qn)}；点「在浏览器打开 → 哔哩哔哩」登录，可以下 {QUALITY.get(best, best)}"
    return Item(
        platform=NAME,
        id=st["bvid"] + (f"_p{st['p']}" if multi else ""),
        title=title,
        url=f"{HOST}/video/{st['bvid']}/" + (f"?p={st['p']}" if multi else ""),
        author=st["owner"] or "",
        created=st["pubdate"] or 0,
        duration=float(st.get("duration") or 0),
        cover=(st.get("pic") or "").replace("http://", "https://"),
        video=video,
        audio=audio,
        video_alt=video_alt,
        audio_alt=audio_alt,
        quality=QUALITY.get(qn, ""),
        headers={"Referer": HOME, "User-Agent": st["ua"]},
        note=note,
    )
