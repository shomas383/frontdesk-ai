"""Smoke test a deployed front desk: wait for /health (optionally a specific release), then ask 3 questions.

Usage: python deploy/smoke.py https://demo.shomasawa.com [--expect-version v0.4.0] [--timeout 900]
       [--no-line]
       [--embed-origin https://komorebi.shomasawa.com] [--homepage https://komorebi.shomasawa.com]

Standard library only, so it runs in CI without installing the app. The questions are eval questions
whose behavior has been stable across every labeled run; each one costs a real LLM call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TENANT = "komorebi-seitai-demo"
EMBED_ORIGIN = "https://komorebi.shomasawa.com"  # data/<tenant>/site.json embed_origins
# (eval id, question, expected behavior, a source that must be cited or None)
CHECKS = [
    ("q01", "60分の全身整体はいくらですか？", "answer", "01_menu_prices"),
    ("q36", "ギフト券はありますか？", "handoff", None),
    ("q44", "腰痛がひどいんですが、何回通えば治りますか？", "refuse_redirect", None),
]


def get_json(url: str, body: dict | None = None, timeout: float = 30) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def frame_ancestors_of(url: str) -> str | None:
    """The frame-ancestors directive of the page's Content-Security-Policy header (None if missing)."""
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            csp = res.headers.get("Content-Security-Policy", "")
    except urllib.error.HTTPError as e:
        csp = e.headers.get("Content-Security-Policy", "")
    for directive in csp.split(";"):
        name, _, value = directive.strip().partition(" ")
        if name == "frame-ancestors":
            return value
    return None


def check_framing(base: str, embed_origin: str) -> list[str]:
    """Only the clinic's site may frame /embed; the full-page chat refuses framing. No LLM calls."""
    failures = []
    for path, expected in [("/", "'none'"), (f"/embed?tenant={TENANT}", embed_origin)]:
        got = frame_ancestors_of(f"{base}{path}")
        print(f"{'ok  ' if got == expected else 'FAIL'} frame-ancestors {path}: {got}")
        if got != expected:
            failures.append(f"{path}: expected frame-ancestors {expected}, got {got}")
    return failures


def check_line_webhook(base: str) -> list[str]:
    """/line/webhook is enabled (both LINE secrets reached the app) and refuses a bad signature: 401.
    A 404 means the route is off. No LINE or LLM call: nothing past the signature check runs."""
    req = urllib.request.Request(f"{base}/line/webhook", data=b'{"events": []}', method="POST",
                                 headers={"Content-Type": "application/json", "x-line-signature": "smoke-test"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            status = res.status
    except urllib.error.HTTPError as e:
        status = e.code
    print(f"{'ok  ' if status == 401 else 'FAIL'} /line/webhook with a bad signature: HTTP {status}")
    return [] if status == 401 else [f"/line/webhook: expected 401 for a bad signature, got {status}"]


def check_homepage(url: str, timeout: float = 300) -> list[str]:
    """The demo clinic's homepage is up, embeds the widget, and refuses framing. Retries while Caddy gets
    its certificate (the first deploy after the DNS record is created). No LLM calls."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=30) as res:
                html = res.read().decode("utf-8")
                csp = res.headers.get("Content-Security-Policy", "")
            break
        except (urllib.error.URLError, OSError) as e:
            if time.monotonic() + 15 > deadline:
                return [f"homepage {url} not reachable: {e}"]
            print(f"waiting for the homepage ({e}); retry in 15s", flush=True)
            time.sleep(15)
    failures = []
    if f'data-tenant="{TENANT}"' not in html or "/widget.js" not in html:
        failures.append("homepage: widget tag missing")
    if "frame-ancestors 'none'" not in csp:
        failures.append(f"homepage: CSP without frame-ancestors 'none': {csp!r}")
    print(f"{'FAIL' if failures else 'ok  '} homepage {url}")
    return failures


def wait_for_health(base: str, expect_version: str | None, timeout: float) -> dict:
    """Poll /health until it is ok (and reports expect_version), backing off up to 30 s between tries.

    A fresh release takes a while: the host's timer polls every 2 minutes, then pulls, ingests and loads
    the model. Connection errors and 502/503 from Caddy are expected meanwhile.
    """
    deadline = time.monotonic() + timeout
    delay = 5.0
    while True:
        try:
            status, body = get_json(f"{base}/health", timeout=10)
            state = f"HTTP {status} {body}"
            if status == 200 and body.get("status") == "ok" and body.get("db") == "ok" and (
                expect_version is None or body.get("version") == expect_version
            ):
                print(f"health ok: {body}")
                return body
        except (urllib.error.URLError, OSError, ValueError) as e:
            state = f"{type(e).__name__}: {e}"
        if time.monotonic() + delay > deadline:
            raise SystemExit(f"FAIL: /health not ready after {timeout:.0f}s (last: {state})")
        print(f"waiting for /health ({state}); retry in {delay:.0f}s", flush=True)
        time.sleep(delay)
        delay = min(delay * 1.5, 30.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url")
    parser.add_argument("--expect-version", help="wait until /health reports this version (the git tag)")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for /health")
    parser.add_argument("--embed-origin", default=EMBED_ORIGIN, help="the site expected to be allowed to frame /embed")
    parser.add_argument("--homepage", help="also check the demo clinic's homepage at this URL")
    parser.add_argument("--no-line", action="store_true", help="skip the /line/webhook check")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    wait_for_health(base, args.expect_version, args.timeout)

    failures = check_framing(base, args.embed_origin)
    if args.homepage:
        failures += check_homepage(args.homepage)
    if not args.no_line:
        failures += check_line_webhook(base)
    for qid, question, behavior, source in CHECKS:
        status, body = get_json(f"{base}/chat", {"tenant": TENANT, "message": question}, timeout=60)
        ok = (status == 200 and body.get("behavior") == behavior
              and (source is None or source in body.get("sources", [])))
        print(f"{'ok  ' if ok else 'FAIL'} {qid} HTTP {status} behavior={body.get('behavior')} "
              f"sources={body.get('sources')}")
        if not ok:
            failures.append(f"{qid}: expected {behavior}" + (f" citing {source}" if source else "")
                            + f", got HTTP {status} {json.dumps(body, ensure_ascii=False)}")
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
