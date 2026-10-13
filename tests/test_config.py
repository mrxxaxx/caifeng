import json
from pathlib import Path

from caifeng.config import ConfigStore, Paths, Settings


def test_missing_file_gives_defaults(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    s = store.load()
    assert s == Settings()
    assert s.appearance == "system"
    assert s.fanqie.delay == 1.0


def test_save_then_load_round_trips(tmp_path: Path):
    store = ConfigStore(tmp_path / "sub" / "config.json")
    s = Settings(browser="edge", appearance="dark")
    s.video.output_dir = "D:/x"
    s.fanqie.scope = "all"
    store.save(s)
    assert store.load() == s


def test_partial_and_unknown_keys_are_tolerated(tmp_path: Path):
    f = tmp_path / "config.json"
    f.write_text(
        json.dumps({"browser": "chrome", "obsolete": 1, "fanqie": {"kind": "drama", "old_field": 2}}), encoding="utf-8"
    )
    s = ConfigStore(f).load()
    assert s.browser == "chrome"
    assert s.fanqie.kind == "drama"
    assert s.fanqie.scope == "free"  # 缺的字段用默认值


def test_corrupt_file_gives_defaults(tmp_path: Path):
    f = tmp_path / "config.json"
    f.write_text("{not json", encoding="utf-8")
    assert ConfigStore(f).load() == Settings()


def test_wrong_types_fall_back_to_defaults(tmp_path: Path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"video": "oops", "fanqie": {"delay": "fast"}}), encoding="utf-8")
    s = ConfigStore(f).load()
    assert s.video == Settings().video
    assert s.fanqie.delay == 1.0


def test_paths_follow_data_dir(tmp_path: Path):
    p = Paths(tmp_path / "data")
    assert p.profile("edge", "douyin") == tmp_path / "data" / "profiles" / "edge" / "douyin"


def test_empty_data_dir_uses_default(tmp_path: Path):
    assert Paths.from_settings(Settings(), default_root=tmp_path).root == tmp_path
    assert Paths.from_settings(Settings(data_dir=str(tmp_path / "d")), default_root=tmp_path).root == tmp_path / "d"


def test_defaults_for_new_ui():
    s = Settings()
    assert s.browser == ""  # 空 = 本机检测到的第一个
    assert s.fanqie.save_mode == "split"
    assert s.output_dir == ""


def test_output_root_defaults_under_downloads(tmp_path: Path):
    from caifeng.config import output_root

    assert output_root(Settings(), downloads=tmp_path) == tmp_path / "采风"
    assert output_root(Settings(output_dir=str(tmp_path / "x")), downloads=tmp_path) == tmp_path / "x"


def test_tool_dir_is_subfolder_of_root_unless_overridden(tmp_path: Path):
    from caifeng.config import tool_dir

    s = Settings(output_dir=str(tmp_path / "root"))
    assert tool_dir(s, "video") == tmp_path / "root" / "视频"
    assert tool_dir(s, "fanqie") == tmp_path / "root" / "番茄"
    s.video.output_dir = str(tmp_path / "elsewhere")
    assert tool_dir(s, "video") == tmp_path / "elsewhere"


def test_bool_fields_reject_non_bool(tmp_path: Path):
    from dataclasses import dataclass

    from caifeng.config import _from_dict

    @dataclass
    class Flag:
        on: bool = True

    assert _from_dict(Flag, {"on": 0}).on is True
    assert _from_dict(Flag, {"on": False}).on is False
