from caifeng.sites.fanqie import (
    book_id_from_url,
    book_info_md,
    chapter_filename,
    html_to_text,
    kind_from_url,
    safe_name,
)


def test_book_id_from_url():
    url = "https://kol.fanqieopen.com/page/content/book-detail?tab_type=2&book_id=7404417471504976921&genre=0"
    assert book_id_from_url(url) == "7404417471504976921"
    assert book_id_from_url("https://x/?bookId=123") == "123"
    assert book_id_from_url("https://kol.fanqieopen.com/page/task") is None
    assert book_id_from_url("") is None


def test_kind_from_url():
    base = "https://kol.fanqieopen.com/page/content/book-detail?top_tab_genre=-1&book_id=1&tab_type="
    assert kind_from_url(base + "2") == "novel"
    assert kind_from_url(base + "6") == "drama"
    assert kind_from_url(base + "16") == "comic"
    assert kind_from_url(base + "99") is None
    assert kind_from_url("https://kol.fanqieopen.com/page/task") is None


def test_html_to_text_cleans_paragraphs_images_and_entities():
    h = '<p>　　第一段&nbsp;A&amp;B</p><p>  </p><div data-fanqie-type="image"><img src="x"></div><p>第二段<br>换行</p>'
    assert html_to_text(h) == "第一段\xa0A&B\n［插图］\n第二段\n换行"


def test_chapter_filename_strips_prefix_and_unsafe_chars():
    assert chapter_filename(1, "第1章 重生") == "001 重生.txt"
    assert chapter_filename(12, "第一百零二章：她来了") == "012 她来了.txt"
    assert chapter_filename(3, "问号?斜杠/") == "003 问号_斜杠_.txt"
    assert chapter_filename(4, "第4章") == "004.txt"


def test_safe_name():
    assert safe_name('a/b:c*?"<>|') == "a_b_c_"
    assert safe_name("   ") == ""


def test_book_info_md_marks_paid_and_failed():
    chapters = [
        {"item_id": "1", "index": 1, "name": "一", "paid": False},
        {"item_id": "2", "index": 2, "name": "二", "paid": False},
        {"item_id": "3", "index": 3, "name": "三", "paid": True},
    ]
    md = book_info_md(
        "99", "书", "作者", ["都市"], {"abstract": "简介"}, 3, 2, 1, 1, chapters, {"1": "已下载", "2": "失败"}
    )
    assert md.startswith("# 书\n")
    assert "- 001　一\n" in md
    assert "- 002　二　［失败］" in md
    assert "- 003　三　［付费］" in md
    assert "本次成功 1 章，失败 1 章" in md


def test_pick_episodes_range():
    from caifeng.sites.fanqie import pick_episodes

    eps = [{"index": i} for i in range(1, 11)]
    assert pick_episodes(eps, None, None) == eps
    assert [e["index"] for e in pick_episodes(eps, 3, 5)] == [3, 4, 5]
    assert [e["index"] for e in pick_episodes(eps, 8, None)] == [8, 9, 10]


def test_book_info_md_for_episodes_has_no_word_count():
    chapters = [{"item_id": "1", "index": 1, "name": "第1集", "paid": False}]
    md = book_info_md("9", "剧", "", [], {}, 1, 1, 1, 0, chapters, {"1": "已下载"}, unit="集")
    assert "字数" not in md
    assert "共 1 集，免费 1 集，本次成功 1 集，失败 0 集" in md


def test_fetch_file_reports_bytes_and_renames(tmp_path):
    import functools
    import http.server
    import threading

    from caifeng.sites.common import fetch_file

    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "v.mp4").write_bytes(b"x" * 700_000)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "srv"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        seen = []
        dst = tmp_path / "001.mp4"
        url = f"http://127.0.0.1:{server.server_port}/v.mp4"
        assert fetch_file(url, dst, on_bytes=lambda got, size: seen.append((got, size)))
        assert dst.stat().st_size == 700_000 and not (tmp_path / "001.mp4.part").exists()
        assert seen[-1] == (700_000, 700_000) and len(seen) >= 3
        # 停止：不留半截文件
        assert not fetch_file(url, tmp_path / "002.mp4", should_stop=lambda: True)
        assert not list(tmp_path.glob("002*"))
    finally:
        server.shutdown()
