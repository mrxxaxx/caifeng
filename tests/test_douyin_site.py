from caifeng.sites.douyin import aweme_id_from_url, best_video, title_of


def test_aweme_id_from_url():
    assert aweme_id_from_url("https://www.douyin.com/video/7694146838416779944") == "7694146838416779944"
    assert aweme_id_from_url("https://www.douyin.com/jingxuan?modal_id=7694146838416779944") == "7694146838416779944"
    assert aweme_id_from_url("https://www.douyin.com/note/7694146838416779945?x=1") == "7694146838416779945"
    assert aweme_id_from_url("https://v.douyin.com/skOH7emgqrc/") is None


def test_best_video_prefers_resolution_then_bitrate():
    def br(h, rate, url):
        return {"bit_rate": rate, "play_addr": {"height": h, "data_size": rate, "url_list": [url]}}

    video = {
        "bit_rate": [br(1024, 1_700_000, "540-high"), br(1280, 900_000, "720-low"), br(1280, 1_600_000, "720-high")]
    }
    assert best_video(video) == ("720-high", 1_600_000)
    assert best_video({"play_addr": {"url_list": ["only"], "data_size": 5}}) == ("only", 5)


def test_title_of():
    assert title_of({"aweme_id": "1", "desc": "铁索连环 #搞笑视频 #抽象"}) == "铁索连环"
    assert title_of({"aweme_id": "1", "item_title": "标题", "desc": "文案"}) == "标题"
