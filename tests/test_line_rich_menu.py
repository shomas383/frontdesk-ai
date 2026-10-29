import importlib.util
import struct

import pytest

from src.config import ROOT_DIR
from src.line import ASK_POSTBACK

_spec = importlib.util.spec_from_file_location("line_rich_menu", ROOT_DIR / "scripts" / "line_rich_menu.py")
menu_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(menu_script)


def test_areas_tile_the_image_exactly():
    menu = menu_script.rich_menu()
    assert menu["size"] == {"width": 2500, "height": 843}
    x = 0
    for area in menu["areas"]:
        b = area["bounds"]
        assert (b["x"], b["y"], b["height"]) == (x, 0, 843) and b["width"] > 0
        x += b["width"]
    assert x == 2500 and len(menu["areas"]) == 4


def test_actions():
    menu = menu_script.rich_menu()
    actions = [a["action"] for a in menu["areas"]]
    assert [a["label"] for a in actions] == ["料金", "営業時間", "アクセス", "質問する"]
    assert [a["text"] for a in actions[:3]] == ["料金を教えてください", "営業時間を教えてください",
                                                "アクセスを教えてください"]
    assert all(a["type"] == "message" for a in actions[:3])
    ask = actions[3]
    assert ask == {"type": "postback", "label": "質問する", "data": ASK_POSTBACK, "inputOption": "openKeyboard"}
    # Messaging API limits
    assert all(len(a["label"]) <= 20 for a in actions)
    assert len(menu["chatBarText"]) <= 14 and len(menu["name"]) <= 300


def test_committed_image_matches_the_menu_and_line_limits():
    data = (ROOT_DIR / "scripts" / "line" / "rich_menu.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (2500, 843)
    assert len(data) <= 1024 * 1024


def test_missing_font_exits_with_install_steps(tmp_path):
    with pytest.raises(SystemExit) as exc:
        menu_script.find_font([tmp_path, tmp_path / "missing"])
    assert "Noto Sans JP" in str(exc.value) and "--font" in str(exc.value)


def test_only_noto_sans_jp_is_searched(tmp_path):
    (tmp_path / "meiryo.ttc").write_bytes(b"x")
    (tmp_path / "YuGothB.ttc").write_bytes(b"x")
    with pytest.raises(SystemExit):
        menu_script.find_font([tmp_path])
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "NotoSansJP-VF.ttf").write_bytes(b"x")
    assert menu_script.find_font([tmp_path]).name == "NotoSansJP-VF.ttf"
