"""widget.js: the one script a clinic adds to its own site. Small, self-contained, no requests of its own."""

import gzip
import re

from src.app import STATIC_DIR
from tests.test_app import make_client

WIDGET = STATIC_DIR / "widget.js"
MAX_BYTES = 8 * 1024
MAX_GZIP_BYTES = 3 * 1024


def widget_js() -> str:
    return WIDGET.read_text(encoding="utf-8")


def test_widget_is_served_as_a_short_cached_script():
    with make_client() as client:
        res = client.get("/widget.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")
    assert res.headers["cache-control"] == "public, max-age=300"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert res.content == WIDGET.read_bytes()  # bytes: a Windows checkout has CRLF


def test_widget_size_budget():
    data = WIDGET.read_bytes()
    assert len(data) <= MAX_BYTES, f"widget.js is {len(data)} bytes (budget {MAX_BYTES})"
    packed = len(gzip.compress(data, 9))
    assert packed <= MAX_GZIP_BYTES, f"widget.js is {packed} bytes gzipped (budget {MAX_GZIP_BYTES})"


def test_widget_makes_no_requests_and_stores_nothing():
    js = widget_js()
    for pattern in (r"\bfetch\s*\(", r"XMLHttpRequest", r"sendBeacon", r"WebSocket", r"EventSource",
                    r"\bimport\s*\(", r"localStorage", r"sessionStorage", r"indexedDB", r"cookie",
                    r"innerHTML", r"insertAdjacentHTML", r"\beval\s*\(", r"new Function"):
        assert not re.search(pattern, js), pattern
    # The only thing it loads is our own /embed page, and only when the panel is opened.
    assert js.count("createElement(tag)") == 1 and js.count('el("iframe"') == 1
    assert "src: `${origin}/embed?tenant=${encodeURIComponent(tenant)}`" in js
    assert 'referrerpolicy: "no-referrer"' in js


def test_widget_finds_our_origin_from_its_own_script_tag():
    js = widget_js()
    assert "const origin = new URL(script.src).origin;" in js
    assert "script.dataset.tenant" in js and "script.dataset.offsetBottom" in js


def test_widget_messages_only_our_frame():
    js = widget_js()
    assert 'postMessage({ type: "frontdesk:focus" }, origin)' in js  # never "*"
    assert js.count("postMessage(") == 1
    assert "if (!frame || event.source !== frame.contentWindow || event.origin !== origin || !data) return;" in js
    # The clinic name from /embed goes into the bar as text.
    assert "name.textContent = data.clinicName.slice(0, 40);" in js


def test_widget_is_accessible_and_has_an_api():
    js = widget_js()
    assert '"aria-label": "AI受付に質問する"' in js and '"aria-expanded"' in js
    assert 'role: "dialog"' in js and '"aria-label": "チャットを閉じる"' in js
    assert 'event.key === "Escape"' in js
    assert re.search(r"open: openPanel,\s*close: closePanel,\s*toggle:", js)
    assert 'attachShadow({ mode: "open" })' in js
