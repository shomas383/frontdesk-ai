"""Which sites may show our chat in an iframe: the CSP frame-ancestors value for GET /embed.

Every other response sends frame-ancestors 'none' (src/app.py). /embed lists the tenant's own sites,
from data/<tenant>/site.json "embed_origins" (plus EXTRA_FRAME_ANCESTORS when testing locally).
The browser enforces it on the framed response; it does not protect the API itself (see README).
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1")


def frame_ancestors(origins: Iterable[str]) -> str:
    """The frame-ancestors source list for these origins, e.g. "https://a.example https://b.example".

    Invalid entries are dropped (fail closed). With nothing valid left, the result is "'none'".
    The spec is tests/test_framing.py.
    """
    valid = [origin for origin in origins if _is_plain_origin(origin)]
    # Keep the configured order and list each origin once; never an empty list, say 'none'.
    return " ".join(dict.fromkeys(valid)) or "'none'"


def _is_plain_origin(origin: str) -> bool:
    """True only for scheme://host[:port], with https (or http on a local host for testing)."""
    if not isinstance(origin, str):
        return False
    # The value goes straight into a response header. Whitespace would add another source, ';' another
    # directive, ',' a second policy, CR/LF another header ("…\r\nX-Injected: 1"), and quotes would turn
    # an entry into a keyword ('self', 'none'). '*' is a wildcard ("*", "https://*.shomasawa.com").
    if any(c.isspace() or c in ";,'\"*" for c in origin):
        return False
    parts = urlsplit(origin)
    try:
        parts.port  # a non-numeric port ("https://a.jp:abc") raises here
    except ValueError:
        return False
    # Exactly scheme://netloc and nothing else: rejects a missing scheme ("komorebi.shomasawa.com"), any
    # path (even a trailing "/"), a query or fragment, "javascript:…", and the scheme-only "https:".
    if f"{parts.scheme}://{parts.netloc}" != origin:
        return False
    # "https://" has no host; "https://user@host" passes the rebuild above but isn't an origin.
    if not parts.hostname or parts.username is not None:
        return False
    # https everywhere (no ftp://, no plain http:// sites); http only for the local browser check.
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)
