"""小红书（www.xiaohongshu.com）：视频笔记和图文笔记，数据在 window.__INITIAL_STATE__.note.noteDetailMap。

2026-10 实测不用登录，但链接要带 xsec_token（App 分享、网页点开的都带），只有笔记 ID 打不开；
连着刷发现页会被风控跳「账号异常」，直接开笔记没事。地址不用带请求头；原图是 HEIC，加 imageView2/format/jpg 拿原尺寸 JPEG。
"""

from __future__ import annotations

import json
import re

from caifeng.sites.common import Item

NAME = "小红书"
HOST = "https://www.xiaohongshu.com"
HOME = HOST + "/explore"
COOKIE_DOMAIN = "xiaohongshu.com"
LOGIN_WALL = "小红书要求登录才能看"  # 游客看多了就弹登录框；界面据此让用户选登录还是换个游客身份
NOTE_ID = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-f]{24})")


def match(url: str) -> bool:
    return "xiaohongshu.com" in url or "xhslink.com" in url


def note_id_from_url(url: str) -> str | None:
    m = NOTE_ID.search(url or "")
    return m.group(1) if m else None


def current(page) -> str | None:
    return page.url if note_id_from_url(page.url) else None  # 保留网址里的 xsec_token，下载时要用


def is_logged_in(page) -> bool:
    return False


JS_NOTE = """
(id) => {
  const s = window.__INITIAL_STATE__;
  const m = s && s.note && s.note.noteDetailMap;
  const map = (m && (m._value || m.value || m)) || {};
  const n = id ? (map[id] || {}).note : Object.values(map).map(x => x && x.note).find(Boolean);
  if (!n || !n.noteId) return null;  // 刚打开时先是个空的占位，等内容填进来
  const st = ((n.video || {}).media || {}).stream || {};
  return { id: n.noteId || id, type: n.type, title: n.title, desc: n.desc, time: n.time, user: (n.user || {}).nickname,
           duration: ((n.video || {}).capa || {}).duration || 0,
           streams: st, images: (n.imageList || []).map(i => ({ url: i.urlDefault, file: i.fileId })) };
}
"""


def best_stream(streams: dict) -> tuple[str, list[str], str]:
    """优先 H.264（兼容性最好），同编码挑分辨率最高的：(地址, 备用地址, 清晰度)。"""
    for codec in ("h264", "h265", "av1"):
        options = [s for s in streams.get(codec) or [] if s.get("masterUrl")]
        if options:
            s = max(options, key=lambda s: (s.get("height") or 0, s.get("size") or 0))
            quality = f"{min(s.get('width') or 0, s.get('height') or 0)}P" if s.get("height") else ""
            return s["masterUrl"], list(s.get("backupUrls") or []), quality
    raise RuntimeError("这条视频笔记没有可下载的地址")


def image_url(img: dict) -> str:
    """原尺寸 JPEG：fileId（没有就从默认地址里截）拼成 imageView2 地址。"""
    file_id = img.get("file") or (img.get("url") or "").split("/")[-1].split("!")[0]
    return f"https://ci.xiaohongshu.com/{file_id}?imageView2/format/jpg" if file_id else img.get("url") or ""


def fetch(page, url: str, timeout: float = 30) -> Item:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)  # 分享短链接 xhslink.com 会跳到笔记页
    if "website-login" in page.url or "/404" in page.url:
        raise RuntimeError("小红书没让看这条笔记（要求登录或验证）：过一会再试，或者点「在浏览器打开 → 小红书」登录")
    nid = note_id_from_url(page.url)
    try:
        # 笔记内容或登录框（游客看多了会弹），哪个先到算哪个
        page.wait_for_function(
            f"() => ({JS_NOTE})({json.dumps(nid)}) || !!document.querySelector('.login-modal')",
            timeout=timeout * 1000,
        )
    except Exception as e:  # noqa: BLE001
        if "xsec_token" not in page.url:
            raise RuntimeError("打不开这条笔记：链接里缺少访问令牌，请用 App 里「分享 → 复制链接」得到的链接") from e
        raise RuntimeError("打开后没有拿到笔记内容：笔记已删除或者设为私密") from e
    if not page.evaluate(JS_NOTE, nid):  # 先等到的是登录框：再给笔记几秒，还没有才算要求登录
        try:
            page.wait_for_function(f"({JS_NOTE})({json.dumps(nid)})", timeout=3000)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{LOGIN_WALL}：点「在浏览器打开 → 小红书」登录一次，再解析") from e
    n = page.evaluate(JS_NOTE, nid)
    title = (n.get("title") or "").strip() or re.sub(r"\s+", " ", re.sub(r"#\S+", "", n.get("desc") or "")).strip()[:40]
    item = Item(
        platform=NAME,
        id=n.get("id") or nid or "",
        title=title,
        url=f"{HOST}/explore/{n.get('id') or nid}",
        author=n.get("user") or "",
        created=(n.get("time") or 0) / 1000,
        duration=float(n.get("duration") or 0),
        cover=((n.get("images") or [{}])[0] or {}).get("url") or "",
    )
    if n.get("type") == "video":
        item.video, item.video_alt, item.quality = best_stream(n.get("streams") or {})
    else:
        imgs = [i for i in n.get("images") or [] if image_url(i)]
        item.images = [image_url(i) for i in imgs]
        item.thumbs = [i.get("url") or image_url(i) for i in imgs]
        item.text = "\n\n".join(x for x in ((n.get("title") or "").strip(), (n.get("desc") or "").strip()) if x)
    return item
