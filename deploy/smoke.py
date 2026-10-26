"""Smoke test a deployed front desk: wait for /health (optionally a specific release), then ask 3 questions.

Usage: python deploy/smoke.py https://demo.shomasawa.com [--expect-version v0.4.0] [--timeout 900]

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
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    wait_for_health(base, args.expect_version, args.timeout)

    failures = []
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
