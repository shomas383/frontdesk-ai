import json
import re

import pytest

from src.app import STATIC_DIR, load_site
from src.config import DATA_DIR, DEFAULT_TENANT
from tests.test_app import make_client

REPO_URL = "https://github.com/shomas383/frontdesk-ai"
BANNER = "架空の整体院のデモです。AIが院の資料をもとに回答します。個人情報は入力しないでください。"
TENANTS_WITH_SITE = sorted(p.parent.name for p in DATA_DIR.glob("*/site.json"))


def test_index_serves_the_chat_page():
    with make_client() as client:
        res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    html = res.text
    assert BANNER in html
    assert "AI front desk demo for a small Japanese clinic" in html
    assert "About this demo" in html and REPO_URL in html
    assert 'src="/static/app.js"' in html and 'href="/static/style.css"' in html


@pytest.mark.parametrize("path, content_type", [
    ("/static/app.js", "javascript"), ("/static/style.css", "text/css"), ("/static/favicon.svg", "image/svg+xml"),
])
def test_static_assets_are_served(path, content_type):
    with make_client() as client:
        res = client.get(path)
    assert res.status_code == 200
    assert content_type in res.headers["content-type"]


def test_security_headers():
    with make_client() as client:
        res = client.get("/")
    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"


def test_page_config():
    with make_client() as client:
        config = client.get("/api/config").json()
    assert config["tenant"] == DEFAULT_TENANT
    assert config["clinic_name"] == "こもれび整体院"
    assert config["max_message_chars"] == 300
    assert config["doc_labels"]["01_menu_prices"] == "料金・メニュー"
    assert config["doc_labels"]["10_campaign_2025"] == "過去のキャンペーン（終了）"


def test_page_config_follows_the_message_limit():
    with make_client(max_message_chars=120) as client:
        assert client.get("/api/config").json()["max_message_chars"] == 120


def test_demo_autosend_is_off_by_default_and_ignored_by_the_page():
    with make_client() as client:
        assert client.get("/api/config").json()["demo_autosend"] is False
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # ?demo= is only read inside runDemo(), which only runs when the server enables it.
    assert js.count('get("demo")') == 1
    assert re.search(r"if \(config\.demo_autosend\) runDemo\(\);", js)
    assert len(re.findall(r"runDemo\(\)", js)) == 2  # definition + the one gated call


def test_demo_autosend_can_be_enabled():
    with make_client(demo_autosend=True) as client:
        assert client.get("/api/config").json()["demo_autosend"] is True


@pytest.mark.parametrize("tenant", TENANTS_WITH_SITE)
def test_doc_labels_cover_every_doc(tenant):
    docs = sorted(p.stem for p in (DATA_DIR / tenant / "docs").glob("*.md"))
    labels = load_site(tenant)["doc_labels"]
    assert sorted(labels) == docs
    assert all(isinstance(v, str) and v.strip() for v in labels.values())


def test_demo_tenant_has_a_site_file():
    assert DEFAULT_TENANT in TENANTS_WITH_SITE
    json.loads((DATA_DIR / DEFAULT_TENANT / "site.json").read_text(encoding="utf-8"))


def test_static_files_use_no_external_resources_or_storage():
    for path in STATIC_DIR.iterdir():
        text = path.read_text(encoding="utf-8")
        urls = set(re.findall(r"https?://[^\s\"'<>)]+", text))
        assert urls <= {REPO_URL, "http://www.w3.org/2000/svg"}, (path.name, urls)
        for api in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            assert api not in text, (path.name, api)
        assert "innerHTML" not in text, path.name
