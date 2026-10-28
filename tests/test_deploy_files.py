"""Production deploy files: Linux line endings and the settings the demo relies on."""

from __future__ import annotations

import re

import pytest

from src.config import ROOT_DIR
from src.eval import DEFAULT_EVAL_TODAY

yaml = pytest.importorskip("yaml")

DEPLOY_DIR = ROOT_DIR / "deploy"
HOST_DIR = ROOT_DIR / "infra" / "host"
BINARY_SUFFIXES = {".webp"}  # the demo homepage's photos (binary in .gitattributes)


def linux_files():
    files = [p for d in (DEPLOY_DIR, HOST_DIR) if d.exists() for p in d.rglob("*") if p.is_file()]
    files += list((ROOT_DIR / "infra").glob("*.tftpl"))
    return [p for p in files if "__pycache__" not in p.parts and p.suffix not in BINARY_SUFFIXES]


def test_linux_files_have_no_carriage_returns():
    # These run on the EC2 host or in containers; a CRLF breaks shebangs, systemd units and cloud-init.
    files = linux_files()
    assert any(p.name == "deploy.sh" for p in files)
    bad = [str(p.relative_to(ROOT_DIR)) for p in files if b"\r" in p.read_bytes()]
    assert not bad, f"CRLF/CR found (check .gitattributes): {bad}"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))


def test_only_caddy_publishes_ports(compose):
    published = {name for name, svc in compose["services"].items() if svc.get("ports")}
    assert published == {"caddy"}


def test_app_environment_matches_the_demo_setup(compose):
    env = compose["services"]["app"]["environment"]
    assert env["APP_TODAY"] == DEFAULT_EVAL_TODAY  # prod answers match the labeled eval runs
    assert env["TRUST_PROXY_HEADERS"] == "true"  # behind Caddy, or every visitor shares one IP
    assert env["DEMO_AUTOSEND"] == "false"
    assert env["LLM_PROVIDER"] == "anthropic"
    assert env["CORS_ORIGINS"] == ""  # the widget's iframe is same-origin with the API
    # Local browser checks only: in production, only site.json's embed_origins may frame /embed.
    assert "EXTRA_FRAME_ANCESTORS" not in (DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8")


def test_app_runs_one_worker_without_access_log(compose):
    command = " ".join(compose["services"]["app"]["command"])
    assert "--workers 1" in command and "--no-access-log" in command
    assert "src.ingest" in command and "--if-empty" not in command  # DB always matches the image's docs


def test_caddy_serves_the_demo_homepage(compose):
    caddy = compose["services"]["caddy"]
    assert caddy["environment"]["HOMEPAGE_DOMAIN"] == "${HOMEPAGE_DOMAIN:-komorebi.shomasawa.com}"
    assert "./sites/komorebi:/srv/komorebi:ro" in caddy["volumes"]
    assert (DEPLOY_DIR / "sites" / "komorebi" / "index.html").is_file()
    caddyfile = (DEPLOY_DIR / "Caddyfile").read_text(encoding="utf-8")
    block = caddyfile[caddyfile.index("{$HOMEPAGE_DOMAIN} {"):]
    assert "root * /srv/komorebi" in block and "file_server" in block
    csp = dict(d.strip().split(" ", 1) for d in re.search(r'Content-Security-Policy "([^"]+)"', block).group(1).split(";"))
    assert csp["default-src"] == "'none'" and csp["frame-ancestors"] == "'none'"
    assert csp["script-src"] == "'self' https://{$DOMAIN}" and csp["frame-src"] == "https://{$DOMAIN}"
    assert "unsafe" not in block


def test_deploy_restarts_caddy_every_time():
    # Its mounts point into the release directory, which every deploy replaces (see deploy.sh).
    lines = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8").splitlines()
    assert "compose restart caddy" in lines  # unindented: not inside a condition


def test_every_service_logs_to_cloudwatch(compose):
    for name, svc in compose["services"].items():
        assert svc["logging"]["driver"] == "awslogs", name


def caddy_filter_deletes(caddyfile: str) -> set[str]:
    """Field names deleted by the `format filter { ... }` block of the default logger."""
    lines = [line.strip() for line in caddyfile.splitlines()]
    start = lines.index("format filter {") + 1
    end = lines.index("}", start)
    return {line.removesuffix(" delete") for line in lines[start:end] if line.endswith(" delete")}


def test_caddy_has_no_access_log_and_strips_client_addresses():
    caddyfile = (DEPLOY_DIR / "Caddyfile").read_text(encoding="utf-8")
    deleted = caddy_filter_deletes(caddyfile)
    # Inside the request a proxy error carries (e.g. a 502 during a redeploy).
    assert {"request>remote_ip", "request>remote_port", "request>client_ip", "request>headers"} <= deleted
    # At the top level, e.g. "remote" (the Let's Encrypt validator) in "served key authentication".
    assert {"remote", "remote_ip", "client_ip"} <= deleted
    lines = [line.strip() for line in caddyfile.splitlines() if not line.strip().startswith("#")]
    assert [line for line in lines if line.startswith("log")] == ["log default {"]
