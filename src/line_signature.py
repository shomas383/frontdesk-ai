"""LINE webhook signature check (x-line-signature), run before the request body is parsed.

LINE signs every webhook request with the channel secret. The route in src/line.py passes the raw
request body and the header value here and rejects the request (401) unless this returns True.
The spec is tests/test_line_signature.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    """True only if `signature` is the x-line-signature LINE would send for exactly these `body` bytes."""
    # An empty secret would make HMAC keyed with "" — anyone could compute that, so it proves nothing.
    # A missing or empty header means the request wasn't signed at all.
    if not channel_secret or not signature:
        return False

    # The header must be ASCII: base64 only uses A–Z a–z 0–9 + / =. Encoding it to bytes here also
    # matters for the comparison below: compare_digest raises TypeError on a str with non-ASCII
    # characters, and nothing a client sends may raise.
    try:
        given = signature.encode("ascii")
    except UnicodeEncodeError:
        return False

    # What LINE computed: HMAC-SHA256 keyed with the channel secret, over the body bytes exactly as
    # they arrived (no decoding, no newline or Unicode normalization, no JSON round trip; any of
    # those changes the bytes and so the signature), then standard base64 with padding.
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest)

    # Compare the base64 text byte for byte rather than decoding the header: decoding is lenient
    # (b64decode ignores stray characters by default), and an exact match is all we need.
    # compare_digest takes the same time wherever the first difference is, so an attacker can't
    # find the right signature one character at a time by timing responses, as they could with ==.
    return hmac.compare_digest(expected, given)
