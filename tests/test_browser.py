from caifeng.browser import BROWSERS, pick

EDGE, CHROME = BROWSERS


def test_pick_configured_browser_when_installed():
    assert pick("chrome", [EDGE, CHROME]) is CHROME


def test_pick_falls_back_to_first_installed():
    assert pick("", [EDGE, CHROME]) is EDGE
    assert pick("chrome", [EDGE]) is EDGE  # 选的没装
    assert pick("builtin", [CHROME]) is CHROME  # 旧配置里的「内置浏览器」


def test_pick_none_when_nothing_installed():
    assert pick("edge", []) is None
