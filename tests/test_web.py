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
    assert "架空の整体院のデモ・個人情報は入力しないでください" in html  # compact banner after the first question
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


HOMEPAGE_ORIGIN = "https://komorebi.shomasawa.com"


def csp_directives(header: str) -> dict[str, str]:
    return dict(part.strip().split(" ", 1) for part in header.split(";"))


def test_security_headers():
    with make_client() as client:
        res = client.get("/")
    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("method, path", [
    ("GET", "/"), ("GET", "/api/config"), ("GET", "/health"), ("GET", "/static/app.js"),
    ("GET", "/static/embed.html"),  # the same page without the per-tenant header: never framable
    ("GET", "/no-such-page"), ("POST", "/chat"),
])
def test_everything_but_embed_refuses_framing(method, path):
    with make_client() as client:
        res = client.request(method, path, json={"tenant": "other-clinic", "message": "x"})
    assert csp_directives(res.headers["content-security-policy"])["frame-ancestors"] == "'none'"
    assert res.headers["x-frame-options"] == "DENY"


def test_embed_can_be_framed_by_the_tenants_own_site():
    with make_client() as client:
        main = client.get("/")
        res = client.get("/embed", params={"tenant": DEFAULT_TENANT})
    embed_csp = csp_directives(res.headers["content-security-policy"])
    assert embed_csp["frame-ancestors"] == HOMEPAGE_ORIGIN
    # Only frame-ancestors differs from the full page's policy.
    main_csp = csp_directives(main.headers["content-security-policy"])
    assert {**embed_csp, "frame-ancestors": "'none'"} == main_csp
    assert "x-frame-options" not in res.headers  # it can't list sites; old browsers would block the clinic too
    assert res.headers["x-content-type-options"] == "nosniff"


def test_extra_frame_ancestors_are_added_for_local_testing():
    with make_client(extra_frame_ancestors=("http://127.0.0.1:8766",)) as client:
        res = client.get("/embed", params={"tenant": DEFAULT_TENANT})
    assert csp_directives(res.headers["content-security-policy"])["frame-ancestors"] == (
        f"{HOMEPAGE_ORIGIN} http://127.0.0.1:8766")


def test_tenant_without_embed_origins_cannot_be_framed():
    with make_client(allowed_tenants=(DEFAULT_TENANT, "no-site-clinic")) as client:
        res = client.get("/embed", params={"tenant": "no-site-clinic"})
    assert res.status_code == 200
    assert csp_directives(res.headers["content-security-policy"])["frame-ancestors"] == "'none'"


def test_page_config():
    with make_client() as client:
        config = client.get("/api/config").json()
    assert config["tenant"] == DEFAULT_TENANT
    assert config["clinic_name"] == "こもれび整体院"
    assert config["max_message_chars"] == 300
    assert config["doc_labels"]["01_menu_prices"] == "料金・メニュー"
    assert config["doc_labels"]["10_campaign_2025"] == "過去のキャンペーン（終了）"


def test_embed_serves_the_compact_chat():
    with make_client() as client:
        res = client.get("/embed", params={"tenant": DEFAULT_TENANT})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    html = res.text
    assert '<body class="embed">' in html
    assert "架空の整体院のデモ・個人情報は入力しないでください" in html  # the disclaimer stays visible
    assert 'src="/static/app.js"' in html and 'href="/static/style.css"' in html


@pytest.mark.parametrize("params", [{}, {"tenant": ""}, {"tenant": "other-clinic"}])
def test_embed_needs_an_allowed_tenant(params):
    with make_client() as client:
        res = client.get("/embed", params=params)
    assert res.status_code == 404
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]


def test_page_config_for_a_given_tenant():
    with make_client() as client:
        assert client.get("/api/config", params={"tenant": DEFAULT_TENANT}).json()["tenant"] == DEFAULT_TENANT
        assert client.get("/api/config", params={"tenant": "other-clinic"}).status_code == 404
        assert client.get("/api/config", params={"tenant": ""}).status_code == 404


def test_embed_mode_talks_to_the_widget_without_data():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # The only messages: an empty close request to the parent, and focus requests accepted from the parent only.
    assert js.count("postMessage(") == 1
    assert 'parent.postMessage({ type: "frontdesk:close" }, "*")' in js
    assert 'event.source === parent && event.data && event.data.type === "frontdesk:focus"' in js


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
