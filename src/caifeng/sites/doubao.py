"""豆包（www.doubao.com）：短视频的一个平台（见 video.py），下载分享出来的对话里生成的视频。

豆包只能分享对话（/thread/…）。对话数据在页面一个 <script data-fn-args> 的属性里（2026-10 实测，不用登录）：
share_info.share_name 是对话标题，message_list 里助手回复的 creation_block.creations[].video 是生成的视频，
同一个视频在数据里会出现好几次（按 vid 去重）。
一段对话里有好几个视频时，由界面列出来让用户挑（见 worker.cmd_download_video）。

无水印（完整版自用）：分享页给的地址都印着「豆包AI生成」。每个视频的 video_model 里还有一个 fallback_api
（字节视频服务取播放地址的接口），把 logo_type 改成 unwatermarked 再请求，返回的就是不加标识的原片，码率也高得多。
返回的地址是加密的（以 qAAB 开头）：AES-128-CBC，密钥由响应里的 key_seed 和一段固定常量算出来。
做法来自 github.com/ihmily/doubao-nomark，2026-10 实测可用；拿不到就退回带标识的地址。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from caifeng.sites.common import Item

NAME = "豆包"
HOST = "https://www.doubao.com"
HOME = HOST + "/chat/"

JS_ARGS = "() => [...document.querySelectorAll('script[data-fn-args]')].map(s => s.getAttribute('data-fn-args'))"


def match(url: str) -> bool:
    return "doubao.com/thread/" in url


def current(page) -> str | None:
    return page.url if match(page.url) else None


def share_data(args: list[str]) -> dict:
    """在几段 data-fn-args 里找到对话分享的数据（含 share_info 和 message_snapshot）。

    豆包会调整嵌套层级（2026-10 起有时包进 routerDataFnArgs 的 JSON 字符串里），所以整份数据挨个找，字符串形式的 JSON 也展开。
    """
    stack: list = list(args)
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            if x[:1] in "[{":
                with contextlib.suppress(ValueError):
                    stack.append(json.loads(x))
        elif isinstance(x, dict):
            if isinstance(x.get("data"), dict) and "message_snapshot" in x["data"]:
                return x["data"]
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return {}


def _blocks(message: dict) -> list:
    try:
        return json.loads(message.get("content") or "[]")
    except ValueError:
        return []


def videos_in(data: dict) -> list[tuple[dict, int]]:
    """对话里生成的视频，按出现顺序、按 vid 去重：[(video, 消息时间)]。"""
    out, seen = [], set()
    for m in (data.get("message_snapshot") or {}).get("message_list") or []:
        blocks = _blocks(m)
        for b in blocks if isinstance(blocks, list) else []:
            block = ((b or {}).get("content") or {}).get("creation_block") or {}
            for c in block.get("creations") or []:
                v = c.get("video") or {}
                key = v.get("vid") or v.get("download_url")
                if key and key not in seen:
                    seen.add(key)
                    out.append((v, int(m.get("create_time") or 0)))
    return out


def _model(v: dict) -> dict:
    """播放器信息 video_model：有时是 JSON 字符串，解析不了就当没有。"""
    model = v.get("video_model") or {}
    if isinstance(model, str):
        try:
            model = json.loads(model)
        except ValueError:
            return {}
    return model if isinstance(model, dict) else {}


def video_urls(v: dict) -> list[str]:
    """下载地址：download_url 在前，播放器用的各路地址（base64 编码的）作备用。"""
    urls = [v.get("download_url") or ""]
    model = _model(v)
    for x in sorted((model.get("video_list") or {}).values(), key=lambda x: -(x.get("vheight") or 0)):
        with contextlib.suppress(ValueError):
            urls.append(base64.b64decode(x.get("main_url") or "").decode())
    return list(dict.fromkeys(u for u in urls if u.startswith("http")))


# ---- 无水印 ----
PRODUCER = "001191110102MACQD9K64010000"  # 豆包写在文件 AIGC 标签里的生成平台编码（2026-10 从带标识的文件里看到）
CLEAN_PARAMS = {"codec_type": "8", "logo_type": "unwatermarked"}
API_HOSTS = (".snssdk.com", ".douyinvod.com", ".dola.com", ".byteintlapi.com")  # 只请求字节自己的视频接口
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
QAAB_SALT = bytes.fromhex(
    "4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524"
    "f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58"
)


def aigc_tag(produce_id: str = "") -> str:
    """AIGC 隐式标识（GB 45438）的默认值：读不到平台原值时用。Label 1 = AI 生成。"""
    return json.dumps(
        {"Label": "1", "ContentProducer": PRODUCER, "ProduceID": produce_id, "ReservedCode1": ""}, ensure_ascii=False
    )


def clean_api(api: str) -> str:
    """fallback_api 改成要无水印的版本；不是字节的视频接口就返回空。"""
    u = urllib.parse.urlsplit(api or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not u.path.startswith("/video/fplay/") or not any(host.endswith(h) for h in API_HOSTS):
        return ""
    query = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k not in CLEAN_PARAMS]
    return urllib.parse.urlunsplit(
        (u.scheme, u.netloc, u.path, urllib.parse.urlencode(query + list(CLEAN_PARAMS.items())), "")
    )


def _b64(text: str) -> bytes:
    """宽松的 base64：有的地址把 + / = 换成了 $ @ #。"""
    for t in (text, text.translate(str.maketrans("$@#", "+/="))):
        t = t.strip().replace("-", "+").replace("_", "/")
        with contextlib.suppress(ValueError):
            return base64.b64decode(t + "=" * (-len(t) % 4), validate=True)
    return b""


def _as_url(raw: bytes) -> str:
    text = raw.decode("ascii", "ignore").strip()
    return text if text.startswith(("http://", "https://")) and text.isprintable() else ""


def decode_url(token: str, key_seed: str = "") -> str:
    """播放地址：明文、base64，或者 qAAB 开头的加密地址。解不出来返回空。"""
    token = (token or "").strip()
    if token.startswith(("http://", "https://")):
        return token
    if url := _as_url(_b64(token)):
        return url
    data, seed = _b64(token), _b64(key_seed)
    if not token.startswith("qAAB") or not data or not seed:
        return ""
    material = hashlib.sha512(hashlib.sha512(seed[:32]).digest() + QAAB_SALT).digest()
    key, iv = material[:16], material[16:32]
    tries = [(data, key, iv)]
    if data.startswith(b"\xa8\x00\x01\x00"):  # 带头部的格式，偏移和 IV 有几种可能
        tries = [(data[4:], key, iv), (data[4:], iv, key)]
        if len(data) > 36:
            tries += [(data[36:], key, data[20:36]), (data[36:], key, iv)]
    for payload, k, v in tries:
        if payload and len(payload) % 16 == 0:
            plain = aes_cbc_decrypt(payload, k, v)
            pad = plain[-1] if plain else 0
            for cand in (plain, plain[:-pad] if 1 <= pad <= 16 else plain):
                if url := _as_url(cand):
                    return url
    return ""


def clean_video(v: dict, timeout: float = 20) -> str:
    """这个视频不带标识的地址；拿不到返回空。"""
    api = clean_api(_model(v).get("fallback_api") or "")
    if not api:
        return ""
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers={"user-agent": UA}), timeout=timeout) as r:
            payload = json.loads(r.read())
    except OSError, ValueError:
        return ""
    info = payload.get("video_info") or (payload.get("data") or {}).get("video_info") or payload
    data = (info.get("data") or info) if isinstance(info, dict) else {}
    found = data.get("video_list") or {}
    entries = [e for e in (found.values() if isinstance(found, dict) else found) if isinstance(e, dict)]
    if not entries:
        return ""
    best = max(entries, key=lambda e: ((e.get("vwidth") or 0) * (e.get("vheight") or 0), e.get("bitrate") or 0))
    key_seed = str(data.get("key_seed") or info.get("key_seed") or payload.get("key_seed") or "")
    return decode_url(best.get("main_url") or best.get("play_url") or "", key_seed)


# AES-128 解密（只为解开几百字节的地址，不值得为它加一个加密库依赖）
def _sbox() -> tuple[list[int], list[int]]:
    sbox, inv = [0] * 256, [0] * 256
    p = q = 1
    while True:
        p ^= ((p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = (
            q
            ^ ((q << 1 | q >> 7) & 0xFF)
            ^ ((q << 2 | q >> 6) & 0xFF)
            ^ ((q << 3 | q >> 5) & 0xFF)
            ^ ((q << 4 | q >> 4) & 0xFF)
        )
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    for i, x in enumerate(sbox):
        inv[x] = i
    return sbox, inv


SBOX, INV_SBOX = _sbox()


def _mul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF
        b >>= 1
    return r


def _round_keys(key: bytes) -> list[list[int]]:
    w = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[x] for x in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _mul(rcon, 2)
        w.append([a ^ b for a, b in zip(w[i - 4], t, strict=True)])
    return [sum(w[4 * r : 4 * r + 4], []) for r in range(11)]


def _decrypt_block(block: bytes, keys: list[list[int]]) -> list[int]:
    s = [a ^ b for a, b in zip(block, keys[10], strict=True)]  # 按列存：s[行 + 4 * 列]
    for r in range(9, -1, -1):
        s = [INV_SBOX[s[row + 4 * ((col - row) % 4)]] for col in range(4) for row in range(4)]
        s = [a ^ b for a, b in zip(s, keys[r], strict=True)]
        if r:
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c : 4 * c + 4]
                out += [
                    _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9),
                    _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13),
                    _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11),
                    _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14),
                ]
            s = out
    return s


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    keys = _round_keys(key)
    out, prev = bytearray(), iv
    for i in range(0, len(data), 16):
        block = data[i : i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, keys), prev, strict=True))
        prev = block
    return bytes(out)


def fetch(page, url: str, timeout: float = 30, nomark: bool = False) -> list[Item]:
    """nomark：去换不带 AI 标识的地址（设置里打开了才换）。"""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_function("() => document.querySelector('script[data-fn-args]')", timeout=timeout * 1000)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("打开后没有拿到对话内容：分享链接失效，或者对话已删除") from e
    data = share_data(page.evaluate(JS_ARGS))
    title = ((data.get("share_info") or {}).get("share_name") or "豆包对话").strip()
    found = videos_in(data)
    if not found:
        raise RuntimeError("这段对话里没有生成的视频")
    clean = [""] * len(found)
    if nomark:
        with ThreadPoolExecutor(max_workers=4) as pool:  # 一段对话可能有十几个视频，一起去换无水印地址
            clean = list(pool.map(lambda x: clean_video(x[0]), found))
    items = []
    for i, ((v, created), clean_url) in enumerate(zip(found, clean, strict=True), 1):
        urls = list(dict.fromkeys([clean_url, *video_urls(v)] if clean_url else video_urls(v)))
        if not urls:
            continue
        w, h = v.get("width") or 0, v.get("height") or 0
        best = max((_model(v).get("video_list") or {}).values(), key=lambda x: x.get("vheight") or 0, default={})
        w, h = best.get("vwidth") or w, best.get("vheight") or h
        cover = ((v.get("cover") or {}).get("image_thumb") or {}).get("url", "")
        items.append(
            Item(
                platform=NAME,
                id=v.get("vid") or "",
                title=title if len(found) == 1 else f"{title} {i}",
                url=url,
                created=created,
                duration=float(v.get("duration") or 0),
                cover=cover,
                video=urls[0],
                video_alt=urls[1:],
                quality=f"{min(int(w), int(h))}P" if w and h else "",
                note="带 AI 标识（没拿到无水印版本）" if nomark and not clean_url else "",
                # 无水印版本连文件里的隐式标识也没有：下载后从带标识的那一路读出原值写回去
                tags={"AIGC": aigc_tag(v.get("vid") or "")} if clean_url else {},
                tags_from=video_urls(v)[0] if clean_url and video_urls(v) else "",
            )
        )
    return items
