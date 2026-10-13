"""番茄达人中心（kol.fanqieopen.com）：下载小说章节和短剧/漫剧剧集。

网关拒绝非浏览器请求（同样的 cookie 用 requests 返回空），但已登录页面里 fetch 可以，且不用 msToken / a_bogus，
所以接口都在 page.evaluate 里调。短剧、漫剧共用章节接口（content_tab 不同），只有免费集给视频地址，自带签名可直接下载。
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from pathlib import Path

from caifeng.sites.common import fetch_file, safe_name

HOST = "https://kol.fanqieopen.com"
APP = "app_id=457699&aid=457699&origin_app_id=457699&host_app_id=457699"
HOME = f"{HOST}/page/task"
LOGIN_REQUIRED = True  # 达人中心后台，没账号进不去
TAB_NOVEL = 2
# 类型 → (content_tab, genre)。查书籍信息时 genre 要对上，否则查不到；章节/剧集接口三种类型通用
TABS = {"novel": (2, 0), "drama": (6, 203), "comic": (16, 205)}
KINDS = {tab: kind for kind, (tab, _) in TABS.items()}  # 详情页网址里的 tab_type → 类型
KIND_NAMES = {"novel": "小说", "drama": "短剧", "comic": "漫剧"}

UNSAFE = re.compile(r'[\\/:*?"<>|\r\n]+')
# 章名开头的「第1章」「第一百零二章」：文件名里已有序号，去掉免得重复
CHAPTER_PREFIX = re.compile(r"^\s*第\s*[0-9零〇一二三四五六七八九十百千两]+\s*章[\s:：、.，,-]*")
BOOK_ID = re.compile(r"[?&#](?:book_id|bookId|bookid)=(\d+)")
TAB_TYPE = re.compile(r"[?&#]tab_type=(\d+)")

Log = Callable[[str], None]
Progress = Callable[[int, int], None]
Stop = Callable[[], bool]


MIN_DELAY = 1.0  # 章与章、集与集之间至少隔几秒，设置里填得再小也按这个来


def detail_url(book_id: str, tab: int = TAB_NOVEL) -> str:
    return f"{HOST}/page/content/book-detail?tab_type={tab}&top_tab_genre=-1&book_id={book_id}&genre=0"


def book_id_from_url(url: str) -> str | None:
    m = BOOK_ID.search(url or "")
    return m.group(1) if m else None


def kind_from_url(url: str) -> str | None:
    """详情页是小说、短剧还是漫剧；认不出来返回 None。"""
    m = TAB_TYPE.search(url or "")
    return KINDS.get(int(m.group(1))) if m else None


JS_LOGGED_IN = (
    """
async () => {
  try {
    const r = await fetch('/api/platform/user/info/v:version?role=1&client_source=0&%s', { credentials: 'include' });
    const j = await r.json();
    return j && j.code === 0 && !!(j.data && (j.data.user_id || j.data.uid || j.data.name || j.data.nick_name));
  } catch (e) { return false; }
}
"""
    % APP
)

JS_BOOK_INFO = (
    """
async ([bookId, tab, genre]) => {
  const out = { name: '', author: '', abstract: '', categories: [], wordNum: 0, score: 0 };
  try {
    const r = await fetch(`/api/platform/content/book/list/by_conf/v1?book_id=${bookId}&content_tab=${tab}&genre=${genre || 0}&%s`, { credentials: 'include' });
    const j = await r.json();
    const b = j && j.data && j.data.book_list && j.data.book_list[0];
    if (b) {
      out.name = b.book_name || '';
      out.author = b.author || b.author_name || '';
      out.abstract = b.book_abstract || b.abstract || '';
      out.categories = (b.categories || []).map(c => c.category_name).filter(Boolean);
      out.wordNum = b.word_num || 0; out.score = b.score || 0;
    }
  } catch (e) {}
  if (!out.name) { const h = document.querySelector('h1, h2'); if (h) out.name = h.textContent.trim(); }
  return out;
}
"""
    % APP
)

JS_CHAPTERS = (
    """
async ([bookId, tab]) => {
  const all = []; let page = 0, total = null;
  while (true) {
    const r = await fetch(`/api/platform/content/chapter/list/v:version?book_id=${bookId}&page_index=${page}&page_size=500&content_tab=${tab}&%s`, { credentials: 'include' });
    const j = await r.json();
    if (!j || j.code !== 0) return { error: JSON.stringify(j).slice(0, 300), chapters: all };
    const list = (j.data && j.data.chapter_list) || [];
    all.push(...list);
    total = (j.data && j.data.total) || total;
    if (!list.length || (total && all.length >= total)) break;
    page++;
    if (page > 20) break;
  }
  return { total, chapters: all.map(c => ({ item_id: String(c.item_id), index: c.index, name: c.chapter_name, paid: !!c.pay_info })) };
}
"""
    % APP
)

JS_DETAIL = (
    """
async ([bookId, itemId, tab]) => {
  const r = await fetch(`/api/platform/content/chapter/detail/v:version?book_id=${bookId}&item_id=${itemId}&content_tab=${tab}&%s`, { credentials: 'include' });
  const j = await r.json();
  if (!j || j.code !== 0 || !j.data) return { error: JSON.stringify(j).slice(0, 300) };
  return { name: j.data.chapter_name, index: j.data.index, content: j.data.content || '',
           video: j.data.video_download_url || j.data.video_url || '' };
}
"""
    % APP
)


def html_to_text(h: str) -> str:
    h = re.sub(r'<div data-fanqie-type="image"[\s\S]*?</div>', "\n［插图］\n", h)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</p>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = html.unescape(h)
    lines = [ln.strip() for ln in h.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def chapter_filename(index, name: str) -> str:
    """「001 章名.txt」，章名去掉「第N章」前缀。"""
    title = safe_name(CHAPTER_PREFIX.sub("", name or ""))
    return f"{int(index):03d} {title}.txt" if title else f"{int(index):03d}.txt"


def book_info_md(book_id, book_name, author, cats, info, total, free_n, ok, fail, chapters, status, unit="章") -> str:
    """000 简介.md：书籍信息 + 完整目录（付费、失败的标出来）。unit 是「章」或「集」。"""
    lines = [
        f"# {book_name}",
        "",
        f"- 作者：{author or '未知'}",
        f"- BookID：{book_id}",
        f"- 分类：{' / '.join(cats) or '无'}",
        *([f"- 字数：{info.get('wordNum') or '未知'}"] if unit == "章" else []),
        f"- 评分：{info.get('score') or '未知'}",
        f"- {'章节' if unit == '章' else '集数'}：共 {total} {unit}，免费 {free_n} {unit}，"
        f"本次成功 {ok} {unit}，失败 {fail} {unit}",
        f"- 下载时间：{time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 简介",
        "",
        (info.get("abstract") or "无").strip(),
        "",
        "## 目录",
        "",
    ]
    for c in chapters:
        state = status.get(c["item_id"]) or ("付费" if c["paid"] else "未下载")
        mark = "" if state == "已下载" else f"　［{state}］"
        lines.append(f"- {int(c['index']):03d}　{c['name']}{mark}")
    return "\n".join(lines) + "\n"


# 以下要在持有浏览器的线程里调用
def ensure_on_site(page) -> None:
    """登录检测要在同域下调接口，页面不在站内就先跳过去。"""
    if not (page.url or "").startswith(HOST):
        page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)


def is_logged_in(page) -> bool:
    try:
        ensure_on_site(page)
        return bool(page.evaluate(JS_LOGGED_IN))
    except Exception:  # noqa: BLE001  页面关了、网络断了都算没登录
        return False


def download_novel(
    page,
    book_id: str,
    out_root: Path,
    *,
    include_paid: bool = False,
    delay: float = MIN_DELAY,
    log: Log = print,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """在已登录的页面上下载一本小说。"""
    page.goto(detail_url(book_id), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)

    info = page.evaluate(JS_BOOK_INFO, [book_id, *TABS["novel"]])
    book_name = (info.get("name") or f"book-{book_id}").strip()
    author = (info.get("author") or "").strip()
    cats = info.get("categories") or []
    log(f"书名：{book_name}" + (f"，作者：{author}" if author else "") + (f"，{' / '.join(cats)}" if cats else ""))

    res = page.evaluate(JS_CHAPTERS, [book_id, TAB_NOVEL])
    if res.get("error"):
        raise RuntimeError(f"读取章节列表失败：{res['error']}")
    chapters = res["chapters"]
    free = [c for c in chapters if not c["paid"]]
    todo = chapters if include_paid else free
    log(f"章节：共 {res.get('total') or len(chapters)} 章，免费 {len(free)} 章，这次下载 {len(todo)} 章")

    out_dir = Path(out_root) / (safe_name(book_name) or book_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    merged = [f"{book_name}\n作者：{author or '未知'}\n"]
    ok = fail = 0
    stopped = False
    for i, c in enumerate(todo, 1):
        if should_stop and should_stop():
            stopped = True
            log("已停止")
            break
        data = None
        for attempt in range(3):
            try:
                data = page.evaluate(JS_DETAIL, [book_id, c["item_id"], TAB_NOVEL])
                if data and not data.get("error") and data.get("content"):
                    break
            except Exception as e:  # noqa: BLE001
                data = {"error": str(e)}
            time.sleep(1 + attempt)
        if not data or data.get("error") or not data.get("content"):
            fail += 1
            status[c["item_id"]] = "失败"
            log(f"✗ 第 {c['index']} 章 {c['name']}：{(data or {}).get('error', '没有正文')}")
        else:
            text = html_to_text(data["content"])
            (out_dir / chapter_filename(c["index"], c["name"])).write_text(f"{c['name']}\n\n{text}\n", encoding="utf-8")
            merged.append(f"\n\n{c['name']}\n\n{text}")
            status[c["item_id"]] = "已下载"
            ok += 1
        if progress:
            progress(i, len(todo))
        time.sleep(max(delay, MIN_DELAY))

    full_file = out_dir / f"000 {safe_name(book_name)}.txt"
    full_file.write_text("".join(merged) + "\n", encoding="utf-8")
    (out_dir / "000 简介.md").write_text(
        book_info_md(
            book_id,
            book_name,
            author,
            cats,
            info,
            res.get("total") or len(chapters),
            len(free),
            ok,
            fail,
            chapters,
            status,
        ),
        encoding="utf-8",
    )
    log(f"完成：成功 {ok} 章，失败 {fail} 章")
    return {
        "ok": ok,
        "fail": fail,
        "out_dir": str(out_dir),
        "full_file": str(full_file),
        "book_name": book_name,
        "stopped": stopped,
        "unit": "章",
    }


def episode_filename(index) -> str:
    return f"{int(index):03d}.mp4"


def pick_episodes(chapters: list[dict], start: int | None, end: int | None) -> list[dict]:
    """start 为空表示全部，end 为空表示到最后一集。"""
    if start is None:
        return list(chapters)
    return [c for c in chapters if int(c["index"]) >= start and (end is None or int(c["index"]) <= end)]


def download_video(
    page,
    book_id: str,
    kind: str,
    out_root: Path,
    *,
    start: int | None = None,
    end: int | None = None,
    save_mode: str = "split",
    delay: float = MIN_DELAY,
    log: Log = print,
    progress: Progress | None = None,
    transfer: Callable[[int, int], None] | None = None,
    should_stop: Stop | None = None,
    merge: Callable[[list[Path], Path, Stop | None], bool] | None = None,
) -> dict:
    """下载短剧/漫剧的免费剧集，已下好的集跳过（停了再下会接着下）。save_mode：split / merge / both。"""
    from caifeng.episodes import check_episodes

    tab, genre = TABS[kind]
    unit_name = KIND_NAMES[kind]
    page.goto(detail_url(book_id, tab), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)

    info = page.evaluate(JS_BOOK_INFO, [book_id, tab, genre])
    name = (info.get("name") or f"{unit_name}-{book_id}").strip()
    cats = info.get("categories") or []
    log(f"{unit_name}：{name}" + (f"，{' / '.join(cats)}" if cats else ""))

    res = page.evaluate(JS_CHAPTERS, [book_id, tab])
    if res.get("error"):
        raise RuntimeError(f"读取剧集列表失败：{res['error']}")
    chapters = res["chapters"]
    total = res.get("total") or len(chapters)
    if not chapters:
        raise RuntimeError(f"没有读到剧集，请确认这是一部{unit_name}（类型选对了吗？）")
    if msg := check_episodes(start or 1, end, total):
        raise RuntimeError(msg)
    free = [c for c in chapters if not c["paid"]]
    wanted = pick_episodes(chapters, start, end)
    todo = [c for c in wanted if not c["paid"]]
    log(
        f"集数：共 {total} 集，免费 {len(free)} 集，这次下载 {len(todo)} 集"
        + (f"（范围内 {len(wanted) - len(todo)} 集付费，跳过）" if len(wanted) > len(todo) else "")
    )
    if not todo:
        raise RuntimeError("选的范围里没有免费剧集，付费集下载不了")

    if progress:
        progress(0, len(todo))
    out_dir = Path(out_root) / (safe_name(name) or book_id)
    # 只要合并：分集先放在临时文件夹，合并好再删
    ep_dir = out_dir / ".分集" if save_mode == "merge" else out_dir
    ep_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    done: list[Path] = []
    ok = fail = 0
    stopped = False
    for i, c in enumerate(todo, 1):
        if should_stop and should_stop():
            stopped = True
            break
        dst = ep_dir / episode_filename(c["index"])
        if dst.exists() and dst.stat().st_size > 0:
            log(f"第 {c['index']} 集已存在，跳过")
        else:
            err = ""
            for attempt in range(3):
                try:
                    data = page.evaluate(JS_DETAIL, [book_id, c["item_id"], tab])
                    if not data or data.get("error") or not data.get("video"):
                        err = (data or {}).get("error") or "没有视频地址"
                    elif fetch_file(data["video"], dst, should_stop, transfer):
                        err = ""
                        break
                    else:
                        stopped = True
                        break
                except Exception as e:  # noqa: BLE001  网络抖动，重试
                    err = str(e)
                time.sleep(1 + attempt)
            if stopped:
                break
            if err:
                fail += 1
                status[c["item_id"]] = "失败"
                log(f"✗ 第 {c['index']} 集：{err}")
                if progress:
                    progress(i, len(todo))
                continue
            log(f"第 {c['index']} 集　{dst.stat().st_size / 1048576:.1f} MB")
            time.sleep(max(delay, MIN_DELAY))
        status[c["item_id"]] = "已下载"
        done.append(dst)
        ok += 1
        if progress:
            progress(i, len(todo))
    if stopped:
        log("已停止")

    merged = None
    if save_mode in ("merge", "both") and done and not stopped and merge:
        whole = start is None or (int(todo[0]["index"]) == 1 and len(done) == len(free))
        label = "" if whole else f" 第{todo[0]['index']}-{todo[-1]['index']}集"
        target = out_dir / f"{safe_name(name)}{label}.mp4"
        log(f"正在合并 {len(done)} 集…")
        if merge(done, target, should_stop):
            merged = str(target)
            log(f"合并完成：{target.name}")
            if save_mode == "merge" and not fail:
                for f in done:
                    f.unlink(missing_ok=True)
                if not any(ep_dir.iterdir()):
                    ep_dir.rmdir()
        else:
            stopped = True
            log("已停止，没有合并")

    (out_dir / "000 简介.md").write_text(
        book_info_md(
            book_id, name, info.get("author") or "", cats, info, total, len(free), ok, fail, chapters, status, unit="集"
        ),
        encoding="utf-8",
    )
    log(f"完成：成功 {ok} 集，失败 {fail} 集")
    return {
        "ok": ok,
        "fail": fail,
        "out_dir": str(out_dir),
        "book_name": name,
        "stopped": stopped,
        "merged": merged,
        "unit": "集",
    }
