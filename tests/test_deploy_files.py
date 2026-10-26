"""Production deploy files: Linux line endings and the settings the demo relies on."""

from __future__ import annotations

import pytest

from src.config import ROOT_DIR
from src.eval import DEFAULT_EVAL_TODAY

yaml = pytest.importorskip("yaml")

DEPLOY_DIR = ROOT_DIR / "deploy"
HOST_DIR = ROOT_DIR / "infra" / "host"


def linux_files():
    files = [p for d in (DEPLOY_DIR, HOST_DIR) if d.exists() for p in d.rglob("*") if p.is_file()]
    files += list((ROOT_DIR / "infra").glob("*.tftpl"))
    return [p for p in files if "__pycache__" not in p.parts]


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


def test_app_runs_one_worker_without_access_log(compose):
    command = " ".join(compose["services"]["app"]["command"])
    assert "--workers 1" in command and "--no-access-log" in command
    assert "src.ingest" in command and "--if-empty" not in command  # DB always matches the image's docs


def test_every_service_logs_to_cloudwatch(compose):
    for name, svc in compose["services"].items():
        assert svc["logging"]["driver"] == "awslogs", name


def test_caddy_has_no_access_log_and_strips_client_addresses():
    caddyfile = (DEPLOY_DIR / "Caddyfile").read_text(encoding="utf-8")
    for field in ("request>remote_ip", "request>client_ip", "request>headers"):
        assert f"{field} delete" in caddyfile
    lines = [line.strip() for line in caddyfile.splitlines() if not line.strip().startswith("#")]
    assert [line for line in lines if line.startswith("log")] == ["log default {"]
