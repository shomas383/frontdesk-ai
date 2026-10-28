"""Spec for src.framing.frame_ancestors: the sites allowed to frame /embed, as a CSP source list."""

import pytest

from src.app import load_embed_origins
from src.config import DATA_DIR
from src.framing import frame_ancestors

OK = "https://komorebi.shomasawa.com"
TENANTS_WITH_SITE = sorted(p.parent.name for p in DATA_DIR.glob("*/site.json"))


def test_no_origins_means_none():
    assert frame_ancestors([]) == "'none'"


def test_origins_are_listed_in_order():
    assert frame_ancestors([OK, "https://example.jp:8443"]) == f"{OK} https://example.jp:8443"


def test_each_origin_is_listed_once():
    assert frame_ancestors([OK, "https://example.jp", OK]) == f"{OK} https://example.jp"


def test_http_only_for_local_hosts():
    assert frame_ancestors(["http://127.0.0.1:8766", "http://localhost:8080"]) == (
        "http://127.0.0.1:8766 http://localhost:8080")
    assert frame_ancestors(["http://komorebi.shomasawa.com"]) == "'none'"


@pytest.mark.parametrize("bad", [
    "",
    "komorebi.shomasawa.com",                 # no scheme
    "https://komorebi.shomasawa.com/",        # a path, even just "/"
    "https://komorebi.shomasawa.com/clinic",
    "https://komorebi.shomasawa.com?x=1",
    "https://komorebi.shomasawa.com#top",
    "https://user@komorebi.shomasawa.com",    # userinfo
    "https://komorebi.shomasawa.com:abc",     # non-numeric port
    "https://*.shomasawa.com",                # wildcard
    "*",
    "https:",                                 # scheme-only source: any https site
    "https://",
    "'self'",
    "'none'",
    "ftp://komorebi.shomasawa.com",
    "javascript:alert(1)",
    "https://a.example.jp https://b.example.jp",  # whitespace: two sources in one entry
    "https://a.example.jp; script-src *",          # a second directive
    "https://a.example.jp,https://b.example.jp",
    "https://a.example.jp\r\nX-Injected: 1",       # a second header
])
def test_invalid_origins_are_dropped(bad):
    assert frame_ancestors([bad]) == "'none'"
    assert frame_ancestors([bad, OK]) == OK


@pytest.mark.parametrize("tenant", TENANTS_WITH_SITE)
def test_configured_embed_origins_are_all_valid(tenant):
    # Invalid entries are dropped silently at runtime, so catch typos in site.json here.
    origins = load_embed_origins(tenant)
    if origins:
        assert frame_ancestors(origins).split() == origins
