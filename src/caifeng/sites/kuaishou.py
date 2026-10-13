"""快手（www.kuaishou.com）：截下作品页自己调的 GraphQL visionVideoDetail 响应，里面有各清晰度的 mp4 地址。

2026-10 实测：UA 带 HeadlessChrome 页面是空的（browser.launch 已处理）；境外 IP 拿到的作品数据是空的（video.DIRECT）；
陌生浏览器来多了会被风控置空数据，一阵一阵的，过会儿或过个滑块就好；视频地址不用带请求头。
"""

from __future__ import annotations

import contextlib
import re

from caifeng.sites.common import Item

NAME = "快手"
HOST = "https://www.kuaishou.com"
HOME = HOST + "/new-reco"
BLOCKED = (
    "快手暂时没给作品信息（作品已删除，或者在风控）：过一会再点重试；或者点「在浏览器打开 → 快手」，过一下滑块验证"
)
# 作品页 /short-video/ID；手机分享页的网址里也常带 photoId
PHOTO_ID = re.compile(r"(?:/short-video/|/photo/|[?&]photoId=)([0-9A-Za-z]{10,})")


def match(url: str) -> bool:
    return any(h in url for h in ("kuaishou.com", "gifshow.com", "chenzhongtech.com", "kwai.com"))


def photo_id_from_url(url: str) -> str | None:
    m = PHOTO_ID.search(url or "")
    return m.group(1) if m else None


# 推荐页的网址和页面上都没有作品 ID。注入脚本监听页面拿到的作品数据，记下「视频文件编号 → 作品 ID」
# （文件名形如 5218827746122182008_5e960e6e457e1494_4436_hd15.mp4，第二段是 manifest.videoId）。只读不改。
INIT_SCRIPT = """
(() => {
  if (!/(^|\\.)kuaishou\\.com$/.test(location.hostname)) return;
  const map = window.__caifengKs = {};
  const parts = u => ((u || '').split('?')[0].split('/').pop() || '').split('_');
  const remember = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 8) return;
    if (typeof o.id === 'string' && (o.manifest || o.photoUrl)) {
      for (const m of [o.manifest, o.manifestH265]) if (m && m.videoId) map[m.videoId] = o.id;
      for (const u of [o.photoUrl, o.photoH265Url]) if (u) map[parts(u).slice(0, 2).join('_')] = o.id;
    }
    for (const k in o) remember(o[k], depth + 1);
  };
  const take = d => { try { remember(d, 0); } catch (e) {} };
  const f = window.fetch;
  window.fetch = async function (...a) { const r = await f.apply(this, a); r.clone().json().then(take, () => {}); return r; };
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...a) {
    this.addEventListener('load', () => {
      try { take(this.responseType === 'json' ? this.response : JSON.parse(this.responseText)); } catch (e) {}
    });
    return send.apply(this, a);
  };
})();
"""

JS_PLAYING = """
() => {
  const map = window.__caifengKs || {};
  const seen = v => { const r = v.getBoundingClientRect(); return r.width * r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
  const videos = [...document.querySelectorAll('video')].filter(seen);
  const v = videos.find(v => !v.paused) || videos[0];
  if (!v) return null;
  const p = ((v.currentSrc || v.src || '').split('?')[0].split('/').pop() || '').split('_');
  return map[p.slice(0, 2).join('_')] || map[p[1]] || null;
}
"""


def current(page) -> str | None:
    pid = photo_id_from_url(page.url)
    if not pid:  # 推荐页：用正在播放的视频去对注入脚本记下的表
        try:
            pid = page.evaluate(JS_PLAYING)
        except Exception:  # noqa: BLE001  页面正在跳转
            pid = None
    return f"{HOST}/short-video/{pid}" if pid else None


def is_logged_in(page) -> bool:
    return False


def _detail(page, url: str, pid: str | None, timeout: float):
    def wanted(r) -> bool:
        post = r.request.post_data or ""
        return "graphql" in r.url and "visionVideoDetail" in post and (not pid or pid in post)

    with page.expect_response(wanted, timeout=timeout * 1000) as resp:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    return ((resp.value.json() or {}).get("data") or {}).get("visionVideoDetail") or {}


def best_url(photo: dict) -> tuple[str, list[str], str]:
    """清晰度最高的一路：(地址, 备用地址, 清晰度)；没有清晰度列表时用 photoUrl。"""
    reps = [r for a in (photo.get("manifest") or {}).get("adaptationSet") or [] for r in a.get("representation") or []]
    reps = [r for r in reps if r.get("url")]
    if reps:
        r = max(reps, key=lambda r: (r.get("height") or 0, r.get("avgBitrate") or 0))
        alt = [*(r.get("backupUrl") or []), photo.get("photoUrl") or ""]
        return r["url"], [u for u in alt if u and u != r["url"]], f"{min(r.get('width') or 0, r.get('height') or 0)}P"
    if photo.get("photoUrl"):
        return photo["photoUrl"], [], ""
    raise RuntimeError("这个作品没有可下载的视频地址（图集作品暂不支持）")


def fetch(page, url: str, timeout: float = 30) -> Item:
    pid = photo_id_from_url(url)
    target = f"{HOST}/short-video/{pid}" if pid else url
    try:
        d = _detail(page, target, pid, timeout)
    except Exception as e:  # noqa: BLE001  短链接跳到了别的页面（手机分享页）：从跳转后的网址里找作品 ID 再开一次
        pid = photo_id_from_url(page.url)
        if not pid:
            raise RuntimeError("打开后没有拿到作品信息：链接失效或者作品已删除") from e
        try:
            d = _detail(page, f"{HOST}/short-video/{pid}", pid, timeout)
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(BLOCKED) from e2
    if not d.get("photo") and pid:  # 风控置空是一阵一阵的，等一下再开一次
        page.wait_for_timeout(3000)
        with contextlib.suppress(Exception):
            d = _detail(page, f"{HOST}/short-video/{pid}", pid, timeout)
    photo = d.get("photo")
    if not photo:
        raise RuntimeError(BLOCKED)
    video, alt, quality = best_url(photo)
    caption = photo.get("caption") or ""
    title = re.sub(r"\s+", " ", re.sub(r"#\S+", "", caption)).strip()
    return Item(
        platform=NAME,
        id=photo.get("id") or pid or "",
        title=title or caption,
        url=f"{HOST}/short-video/{photo.get('id') or pid}",
        author=(d.get("author") or {}).get("name") or "",
        created=(photo.get("timestamp") or 0) / 1000,
        duration=float(photo.get("duration") or 0) / 1000,
        cover=photo.get("coverUrl") or "",
        video=video,
        video_alt=alt,
        quality=quality,
    )
