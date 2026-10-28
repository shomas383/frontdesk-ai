"""The fictional clinic's homepage (deploy/sites/komorebi): says what the docs say, stays light, loads nothing third-party."""

import re

import pytest

from src.app import load_embed_origins
from src.config import DATA_DIR, DEFAULT_TENANT, ROOT_DIR

SITE_DIR = ROOT_DIR / "deploy" / "sites" / "komorebi"
DOCS = DATA_DIR / DEFAULT_TENANT / "docs"
HOMEPAGE_ORIGIN = "https://komorebi.shomasawa.com"
WIDGET_TAG = '<script src="https://demo.shomasawa.com/widget.js" data-tenant="komorebi-seitai-demo"'
ALLOWED_URLS = {
    "https://demo.shomasawa.com/widget.js", "https://demo.shomasawa.com",
    "http://www.w3.org/2000/svg",
}
# Claims the bot itself never makes (medical outcomes, rankings) and content the site must not have.
FORBIDDEN = ["治る", "治り", "治ります", "完治", "根本", "改善", "効果", "保証", "No.1", "満足度", "お客様の声", "口コミ"]
MAX_IMAGE = 200 * 1024
MAX_IMAGES = 700 * 1024
MAX_PAGE = 900 * 1024


@pytest.fixture(scope="module")
def html() -> str:
    return (SITE_DIR / "index.html").read_text(encoding="utf-8")


def text_files():
    return [p for p in SITE_DIR.rglob("*") if p.suffix in (".html", ".css", ".js", ".svg")]


def test_prices_match_the_price_list(html):
    prices = set(re.findall(r"\d[\d,]*円", html))
    assert {"7,150円", "4,980円", "2,200円"} <= prices
    doc = (DOCS / "01_menu_prices.md").read_text(encoding="utf-8")
    assert [p for p in sorted(prices) if p not in doc] == []


def test_hours_match_the_docs(html):
    # The ○/× grid's row labels are time slots, not opening hours; every other range must be in the docs.
    text = re.sub(r'<table class="hours-grid">.*?</table>', "", html, flags=re.S)
    ranges = set(re.findall(r"\d{1,2}:\d{2}〜\d{1,2}:\d{2}", text))
    assert {"10:00〜20:00", "9:00〜18:00", "10:00〜17:00"} <= ranges
    docs = "".join((DOCS / f).read_text(encoding="utf-8") for f in ("02_hours.md", "07_news.md"))
    assert [r for r in sorted(ranges) if r not in docs] == []


def test_hours_grid_follows_the_docs(html):
    grid = re.search(r'<table class="hours-grid">.*?</table>', html, flags=re.S).group(0)
    rows = {th: re.findall(r"<td[^>]*>(.)</td>", row) for th, row in
            re.findall(r'<th scope="row">([^<]+)</th>(.*?)</tr>', grid)}
    #                 月    火    水    木    金    土    日祝
    assert rows == {
        "9:00〜10:00": ["×", "×", "×", "×", "×", "○", "×"],    # only Saturday opens at 9:00
        "10:00〜17:00": ["○", "○", "×", "○", "○", "○", "○"],  # closed on Wednesdays
        "17:00〜20:00": ["○", "○", "×", "○", "○", "▲", "×"],  # ▲ Saturday until 18:00; Sunday until 17:00
    }


def test_no_medical_claims_or_testimonials():
    for path in text_files():
        text = path.read_text(encoding="utf-8")
        assert [w for w in FORBIDDEN if w in text] == [], path.name


def test_clearly_marked_as_a_fictional_demo(html):
    assert "<strong>架空の整体院</strong>のサイトです。<span class=\"nowrap\">お電話</span>・LINEでのご予約はできません。" in html
    assert "実在の院・人物・住所とは関係ありません" in html
    assert html.count('aria-disabled="true"') == 3 and html.count("<small>デモ</small>") == 3
    assert '<meta name="robots" content="noindex">' in html


def test_embeds_the_chat_with_one_script_tag(html):
    assert html.count(WIDGET_TAG) == 1
    assert 'data-offset-bottom="64"' in html and "--callbar-h: 64px" in (SITE_DIR / "style.css").read_text(encoding="utf-8")
    assert HOMEPAGE_ORIGIN in load_embed_origins(DEFAULT_TENANT)  # the API lets this site frame /embed


def test_no_third_party_resources_or_fonts():
    for path in text_files():
        text = path.read_text(encoding="utf-8")
        urls = set(re.findall(r"https?://[^\s\"'<>)]+", text))
        assert urls <= ALLOWED_URLS, (path.name, urls - ALLOWED_URLS)
        for banned in ("@font-face", "@import", "url(http", "innerHTML", "localStorage", "cookie"):
            assert banned not in text, (path.name, banned)


def test_images_are_referenced_sized_and_within_budget(html):
    images = sorted((SITE_DIR / "img").glob("*"))
    assert images and all(p.suffix == ".webp" for p in images)
    for p in images:
        assert p.stat().st_size <= MAX_IMAGE, (p.name, p.stat().st_size)
        tag = re.search(rf'<img [^>]*src="img/{re.escape(p.name)}"[^>]*>', html)
        assert tag, f"{p.name} is not used"
        assert re.search(r'width="\d+" height="\d+"', tag.group(0)) and 'alt="' in tag.group(0)
    assert sum(p.stat().st_size for p in images) <= MAX_IMAGES
    page = sum(p.stat().st_size for p in SITE_DIR.rglob("*") if p.is_file())
    assert page <= MAX_PAGE, f"homepage is {page} bytes (budget {MAX_PAGE})"


def test_photo_credits_are_on_the_page(html):
    for name in ("Ester Avanesyan", "Daniel Chen", "Mayur Roxan", "Sven Mieke", "Ryunosuke Kikuno"):
        assert name in html
