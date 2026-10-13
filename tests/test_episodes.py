from caifeng.episodes import check_episodes


def test_valid_ranges():
    assert check_episodes(1, None, None) == ""  # 到最后一集，总集数还不知道
    assert check_episodes(3, 7, 20) == ""
    assert check_episodes(20, 20, 20) == ""
    assert check_episodes(5, None, 20) == ""


def test_start_must_be_positive():
    assert check_episodes(None, 5, 20) == "起始集数要从 1 开始"
    assert check_episodes(0, 5, 20) == "起始集数要从 1 开始"


def test_end_not_before_start():
    assert check_episodes(8, 3, 20) == "结束集数不能比起始集数小"


def test_not_beyond_total():
    assert check_episodes(1, 25, 20) == "这部剧只有 20 集"
    assert check_episodes(21, None, 20) == "这部剧只有 20 集"
