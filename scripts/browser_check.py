"""Browser check of the demo homepage + chat widget (Playwright), at phone and desktop widths.

Local (default): starts the real app (src.app.create_app) with the test fakes from tests/test_app.py, so
there is no database, embedding model or LLM call, plus the homepage on two ports: one allowed to frame
/embed (via EXTRA_FRAME_ANCESTORS) and one that isn't. The homepage is served with the CSP from
deploy/Caddyfile and its widget tag pointed at the local app.

    python scripts/browser_check.py [--webkit]
    python scripts/browser_check.py --site https://komorebi.shomasawa.com   # live; sends no question

Screenshots go to .browser-check/ (gitignored). Exits 1 if any check fails.
Setup (dev only, not in the image): pip install -r requirements-browser.txt; python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SITE_DIR = ROOT / "deploy" / "sites" / "komorebi"
OUT_DIR = ROOT / ".browser-check"
PROD_WIDGET = "https://demo.shomasawa.com/widget.js"
HOST = "127.0.0.1"
APP_PORT, SITE_PORT, OTHER_PORT = 8765, 8766, 8767
VIEWPORTS = {
    "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True,
               "has_touch": True},
    "desktop": {"viewport": {"width": 1280, "height": 800}},
}
FAKE_ANSWER = "全身整体60分の料金は7,150円（税込）です。初めての方は、別途初検料2,200円がかかります。"
CHIP = "60分の料金は？"


def homepage_csp(app_origin: str) -> str:
    """The homepage's CSP exactly as Caddy sends it, with our app's origin in place of https://{$DOMAIN}."""
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy "([^"]+)"', caddyfile).group(1)
    return csp.replace("https://{$DOMAIN}", app_origin)


def start_app(allowed_origin: str) -> None:
    """The real app with the test fakes: canned retrieval, a fixed answer, in-memory usage."""
    import uvicorn

    import src.answer
    import src.app
    from src.config import AppSettings
    from tests.test_app import FakeProvider, FakeUsage, _retrieval, fake_connect

    ids = ("01_menu_prices#コース料金", "01_menu_prices#初検料")
    src.answer.retrieve = lambda conn, tenant, question, mode, k: _retrieval(*ids)
    provider = FakeProvider({"answer": FAKE_ANSWER, "behavior": "answer", "sources": ["01_menu_prices"]})
    settings = AppSettings(app_today="2026-10-24", extra_frame_ancestors=(allowed_origin,))
    app = src.app.create_app(settings, provider, fake_connect, FakeUsage(), warm=False)
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=APP_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        threading.Event().wait(0.05)


def start_site(port: int, app_origin: str) -> None:
    """Serve the homepage like Caddy does (same CSP), with the widget loaded from the local app."""
    csp = homepage_csp(app_origin)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            self.send_header("Content-Security-Policy", csp)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return super().do_GET()
            html = (SITE_DIR / "index.html").read_text(encoding="utf-8").replace(PROD_WIDGET, f"{app_origin}/widget.js")
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    handler = functools.partial(Handler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer((HOST, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def shot(page, name: str, **kwargs) -> None:
    """Save a screenshot; retry briefly if Windows has the old file open (an image viewer, a thumbnailer)."""
    data = page.screenshot(**kwargs)
    for attempt in range(10):
        try:
            (OUT_DIR / name).write_bytes(data)
            return
        except OSError:
            if attempt == 9:
                raise
            threading.Event().wait(0.5)


class Checker:
    def __init__(self):
        self.failures: list[str] = []

    def check(self, ok: bool, label: str) -> bool:
        print(f"{'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            self.failures.append(label)
        return ok


# Widget internals live in an open shadow root; these read its state from the page.
JS_STATE = """() => {
  const host = [...document.body.children].find((el) => el.shadowRoot && el.shadowRoot.querySelector('.launcher'));
  const root = host.shadowRoot;
  const panel = root.querySelector('.panel');
  const inner = root.activeElement;
  return {
    open: !panel.hidden,
    focus: document.activeElement === host ? (inner ? inner.className || inner.tagName : 'host') :
           (document.activeElement.className || document.activeElement.tagName),
    panel: panel.hidden ? null : panel.getBoundingClientRect().toJSON(),
    launcher: root.querySelector('.launcher').getBoundingClientRect().toJSON(),
  };
}"""


def run_viewport(browser, name: str, site: str, app_origin: str, c: Checker, live: bool) -> None:
    context = browser.new_context(**VIEWPORTS[name], locale="ja-JP")
    requests: list[str] = []
    context.on("request", lambda req: requests.append(req.url))
    page = context.new_page()
    console: list[str] = []
    page.on("console", lambda msg: console.append(msg.text))
    page.goto(site, wait_until="networkidle")
    page.wait_for_function("() => window.FrontdeskWidget && window.FrontdeskWidget.open")
    # Scroll through once so loading="lazy" images are in the full-page screenshot.
    height = page.evaluate("document.body.scrollHeight")
    for y in range(0, height, 600):
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(50)
    page.wait_for_load_state("networkidle")
    page.evaluate("window.scrollTo(0, 0)")
    shot(page, f"{name}-home-full.png", full_page=True)
    shot(page, f"{name}-home.png")

    launcher = page.get_by_role("button", name="AI受付に質問する")
    c.check(launcher.is_visible(), f"{name}: launcher visible with its label")
    state = page.evaluate(JS_STATE)
    c.check(not state["open"], f"{name}: panel starts closed")
    c.check(not any(u.startswith(f"{app_origin}/embed") for u in requests),
            f"{name}: nothing but widget.js is loaded from us before the panel opens")
    if name == "mobile":
        bar = page.locator(".callbar").bounding_box()
        c.check(state["launcher"]["bottom"] <= bar["y"], f"{name}: launcher sits above the sticky call bar")

    # Open from the launcher; the chat loads in the iframe and gets focus.
    launcher.click()
    frame = page.frame_locator("iframe[title='AI受付']")
    frame.locator("#message").wait_for(state="visible", timeout=15000)
    page.wait_for_timeout(300)
    state = page.evaluate(JS_STATE)
    c.check(state["open"], f"{name}: launcher opens the panel")
    c.check(state["focus"] == "IFRAME", f"{name}: focus moves into the panel (got {state['focus']})")
    if name == "mobile":
        vp = VIEWPORTS[name]["viewport"]
        p = state["panel"]
        c.check(p["x"] == 0 and p["y"] == 0 and p["width"] == vp["width"] and p["height"] == vp["height"],
                f"{name}: panel is full screen ({p['width']}x{p['height']})")
    else:
        inner_focus = frame.locator("body").evaluate("() => document.activeElement.id")
        c.check(inner_focus == "message", f"{name}: the question box has focus (got {inner_focus!r})")
    shot(page, f"{name}-open.png")

    if not live:
        frame.get_by_role("button", name=CHIP).click()
        frame.get_by_text(FAKE_ANSWER).wait_for(timeout=15000)
        c.check(True, f"{name}: a suggestion chip gets an answer")
        shot(page, f"{name}-answered.png")

    # Esc inside the chat closes the panel and focus returns to the launcher.
    frame.locator("body").press("Escape")
    page.wait_for_timeout(200)
    state = page.evaluate(JS_STATE)
    c.check(not state["open"], f"{name}: Esc closes the panel")
    c.check(state["focus"] == "launcher", f"{name}: focus returns to the launcher (got {state['focus']})")

    # The site's own button opens it (call bar on phones, header on desktop); the close button closes it.
    ask = page.locator(".callbar .js-ask" if name == "mobile" else ".header-contact .js-ask")
    ask.click()
    page.wait_for_timeout(300)
    c.check(page.evaluate(JS_STATE)["open"], f"{name}: the site's 質問する button opens the panel")
    page.get_by_role("button", name="チャットを閉じる").click()
    page.wait_for_timeout(200)
    state = page.evaluate(JS_STATE)
    c.check(not state["open"], f"{name}: the close button closes the panel")
    c.check("js-ask" in state["focus"], f"{name}: focus returns to the button that opened it (got {state['focus']})")

    allowed = {origin_of(site), app_origin}
    others = sorted({origin_of(u) for u in requests if not u.startswith(("data:", "blob:"))} - allowed)
    c.check(not others, f"{name}: requests only go to the site and us (others: {others})")
    c.check(context.cookies() == [], f"{name}: no cookies")
    stored = [f.evaluate("() => localStorage.length + sessionStorage.length") for f in page.frames
              if f.url.startswith("http")]
    c.check(not any(stored), f"{name}: nothing in web storage on either origin")
    violations = [m for m in console if "Content Security Policy" in m or "Refused" in m]
    c.check(not violations, f"{name}: no CSP violations ({violations[:2]})")
    context.close()


def run_refused(browser, site: str, app_origin: str, c: Checker) -> None:
    """A site that isn't in embed_origins: the widget still loads, but the browser refuses the frame."""
    context = browser.new_context(**VIEWPORTS["desktop"], locale="ja-JP")
    page = context.new_page()
    console: list[str] = []
    page.on("console", lambda msg: console.append(msg.text))
    page.goto(site, wait_until="networkidle")
    page.get_by_role("button", name="AI受付に質問する").click()
    page.wait_for_timeout(2000)
    loaded = any(f.url.startswith(f"{app_origin}/embed") and f.locator("#message").count() for f in page.frames)
    c.check(not loaded, "other site: the chat does not load in its frame")
    refused = [m for m in console if "frame-ancestors" in m]
    c.check(bool(refused), f"other site: the browser reports the frame-ancestors refusal ({refused[:1]})")
    shot(page, "other-site-refused.png")
    page.get_by_role("button", name="チャットを閉じる").click()
    c.check(not page.evaluate(JS_STATE)["open"], "other site: the panel still closes")
    context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", help="check a deployed homepage instead of local servers (sends no question)")
    parser.add_argument("--webkit", action="store_true", help="use WebKit (Safari's engine) instead of Chromium")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(exist_ok=True)
    if args.site:
        site, app_origin, other = args.site.rstrip("/"), origin_of(PROD_WIDGET), None
    else:
        app_origin = f"http://{HOST}:{APP_PORT}"
        site, other = f"http://{HOST}:{SITE_PORT}", f"http://{HOST}:{OTHER_PORT}"
        start_app(allowed_origin=site)
        start_site(SITE_PORT, app_origin)
        start_site(OTHER_PORT, app_origin)

    c = Checker()
    with sync_playwright() as p:
        browser = (p.webkit if args.webkit else p.chromium).launch()
        for name in VIEWPORTS:
            run_viewport(browser, name, site, app_origin, c, live=bool(args.site))
        if other:
            run_refused(browser, other, app_origin, c)
        browser.close()

    print(f"\nscreenshots: {OUT_DIR}")
    print(f"{len(c.failures)} failed" if c.failures else "all checks passed")
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
