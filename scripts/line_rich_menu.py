"""The LINE rich menu (料金 / 営業時間 / アクセス / 質問する): draws its image and sets it as the default menu.

    python scripts/line_rich_menu.py            # dry run: writes scripts/line/rich_menu.png, prints the JSON
    python scripts/line_rich_menu.py --apply    # validate, create, upload the image, set as default,
                                                # then delete older menus this script created

The image is drawn here (flat colors, simple shapes) with Noto Sans JP (SIL Open Font License) as the
only font, so it carries no third-party artwork. --font points at another copy of Noto Sans JP.

The channel access token is read from Parameter Store with the AWS CLI (run `aws login` first), so it is
never pasted or kept in shell history; LINE_CHANNEL_ACCESS_TOKEN in the environment overrides it. The
token is never printed. Needs Pillow (installed with sentence-transformers); otherwise stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.line import ASK_POSTBACK  # noqa: E402  (the webhook answers this postback)

IMAGE_PATH = ROOT / "scripts" / "line" / "rich_menu.png"
MENU_NAME = "frontdesk-ai demo menu"  # how --apply recognizes the menus it created earlier
WIDTH, HEIGHT = 2500, 843  # LINE's compact full-width size
MAX_IMAGE_BYTES = 1024 * 1024  # LINE's limit for a rich menu image
PARAM_NAME = "/frontdesk-ai/prod/LINE_CHANNEL_ACCESS_TOKEN"
AWS_REGION = "us-east-2"
API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"

# (label, action). The first three ask through the normal answer pipeline; 質問する opens the keyboard.
BUTTONS: list[tuple[str, dict[str, Any]]] = [
    ("料金", {"type": "message", "label": "料金", "text": "料金を教えてください"}),
    ("営業時間", {"type": "message", "label": "営業時間", "text": "営業時間を教えてください"}),
    ("アクセス", {"type": "message", "label": "アクセス", "text": "アクセスを教えてください"}),
    ("質問する", {"type": "postback", "label": "質問する", "data": ASK_POSTBACK, "inputOption": "openKeyboard"}),
]

BACKGROUND = "#F6F1E7"
INK = "#2F4A3A"
ACCENT = "#6B8F71"
DIVIDER = "#D9CFBC"

FONT_NAMES = ("NotoSansJP-VF.ttf", "NotoSansJP-Bold.ttf", "NotoSansJP-Regular.ttf",
              "NotoSansJP-VariableFont_wght.ttf", "NotoSansJP[wght].ttf")
INSTALL_HINT = """Noto Sans JP was not found. Install it, or pass --font <path to NotoSansJP*.ttf>:
  - Download it from https://fonts.google.com/noto/specimen/Noto+Sans+JP (SIL Open Font License)
  - Windows: unzip, right-click the .ttf -> "Install for all users" (goes to C:\\Windows\\Fonts)
  - macOS: open the .ttf -> Install; Linux: copy it to ~/.local/share/fonts/
Only Noto Sans JP is used, so the committed image carries no font with unclear redistribution terms."""


def rich_menu() -> dict[str, Any]:
    """The rich menu object: four equal columns across the image."""
    column = WIDTH // len(BUTTONS)
    areas = []
    for i, (_, action) in enumerate(BUTTONS):
        x = i * column
        width = WIDTH - x if i == len(BUTTONS) - 1 else column
        areas.append({"bounds": {"x": x, "y": 0, "width": width, "height": HEIGHT}, "action": action})
    return {"size": {"width": WIDTH, "height": HEIGHT}, "selected": True, "name": MENU_NAME,
            "chatBarText": "メニュー", "areas": areas}


def font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path.home() / ".local/share/fonts",
             Path.home() / ".fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    return dirs


def find_font(dirs: list[Path]) -> Path:
    """The first Noto Sans JP file in `dirs` (searched recursively). Exits with install steps if none."""
    for directory in dirs:
        if not directory.is_dir():
            continue
        for name in FONT_NAMES:
            found = next(iter(sorted(directory.rglob(name))), None)
            if found:
                return found
    raise SystemExit(INSTALL_HINT)


def draw_image(font_path: Path):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 120)
    try:  # the variable font defaults to Thin
        font.set_variation_by_axes([700])
    except (OSError, AttributeError):
        pass
    column = WIDTH // len(BUTTONS)
    icons = [_coin, _clock, _pin, _bubble]
    for i, (label, _) in enumerate(BUTTONS):
        cx = i * column + column // 2
        if i:
            draw.line([(i * column, 120), (i * column, HEIGHT - 120)], fill=DIVIDER, width=6)
        icons[i](draw, cx, 300, font)
        draw.text((cx, 640), label, font=font, fill=INK, anchor="mm")
    return image


def _coin(draw, cx, cy, font) -> None:
    draw.ellipse([cx - 140, cy - 140, cx + 140, cy + 140], outline=ACCENT, width=22)
    draw.text((cx, cy), "¥", font=font, fill=ACCENT, anchor="mm")


def _clock(draw, cx, cy, font) -> None:
    draw.ellipse([cx - 140, cy - 140, cx + 140, cy + 140], outline=ACCENT, width=22)
    draw.line([(cx, cy), (cx, cy - 90)], fill=ACCENT, width=22)
    draw.line([(cx, cy), (cx + 70, cy + 40)], fill=ACCENT, width=22)
    draw.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=ACCENT)


def _pin(draw, cx, cy, font) -> None:
    draw.ellipse([cx - 110, cy - 160, cx + 110, cy + 60], fill=ACCENT)
    draw.polygon([(cx - 95, cy), (cx + 95, cy), (cx, cy + 165)], fill=ACCENT)
    draw.ellipse([cx - 45, cy - 95, cx + 45, cy - 5], fill=BACKGROUND)


def _bubble(draw, cx, cy, font) -> None:
    draw.rounded_rectangle([cx - 170, cy - 130, cx + 170, cy + 90], radius=60, fill=ACCENT)
    draw.polygon([(cx - 90, cy + 80), (cx - 20, cy + 80), (cx - 110, cy + 160)], fill=ACCENT)
    for dx in (-80, 0, 80):
        draw.ellipse([cx + dx - 22, cy - 42, cx + dx + 22, cy + 2], fill=BACKGROUND)


def access_token() -> str:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        out = subprocess.run(["aws", "ssm", "get-parameter", "--region", AWS_REGION, "--name", PARAM_NAME,
                              "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Could not read {PARAM_NAME} with the AWS CLI (run `aws login` first): "
                         f"{getattr(exc, 'stderr', '') or exc}")
    return out.stdout.strip()


def call(method: str, url: str, token: str, body: bytes | None = None,
         content_type: str = "application/json") -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", **({"Content-Type": content_type} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url}: HTTP {e.code} {e.read().decode('utf-8', 'replace')}")
    return json.loads(raw) if raw.strip() else {}


def apply(menu: dict[str, Any], image: bytes, token: str) -> None:
    payload = json.dumps(menu, ensure_ascii=False).encode("utf-8")
    call("POST", f"{API}/richmenu/validate", token, payload)
    print("validated")
    old = [m["richMenuId"] for m in call("GET", f"{API}/richmenu/list", token).get("richmenus", [])
           if m.get("name") == MENU_NAME]
    menu_id = call("POST", f"{API}/richmenu", token, payload)["richMenuId"]
    print(f"created {menu_id}")
    call("POST", f"{DATA_API}/richmenu/{menu_id}/content", token, image, "image/png")
    print("image uploaded")
    call("POST", f"{API}/user/all/richmenu/{menu_id}", token, b"")
    print("set as the default menu")
    for old_id in old:
        call("DELETE", f"{API}/richmenu/{old_id}", token)
        print(f"deleted the older menu {old_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="create the menu on LINE and make it the default")
    parser.add_argument("--font", type=Path, help="path to a Noto Sans JP .ttf (default: search the font folders)")
    args = parser.parse_args()

    font = args.font if args.font else find_font(font_dirs())
    if not font.is_file():
        raise SystemExit(f"--font {font}: no such file")
    IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    draw_image(font).save(IMAGE_PATH, optimize=True)
    image = IMAGE_PATH.read_bytes()
    if len(image) > MAX_IMAGE_BYTES:
        raise SystemExit(f"{IMAGE_PATH} is {len(image)} bytes; LINE allows {MAX_IMAGE_BYTES}")
    menu = rich_menu()
    print(f"wrote {IMAGE_PATH.relative_to(ROOT)} ({len(image) // 1024} KB) with {font.name}")
    print(json.dumps(menu, ensure_ascii=False, indent=2))
    if args.apply:
        apply(menu, image, access_token())
    else:
        print("dry run: pass --apply to create it on LINE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
