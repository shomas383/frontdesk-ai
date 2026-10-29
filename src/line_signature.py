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
    """True only if `signature` is the x-line-signature LINE would send for exactly these `body` bytes.

    TODO(shoma): implement. Hints:
    - The signature is HMAC-SHA256 of the raw request body, keyed with the channel secret, then
      base64-encoded. See "Verify webhook signature" in the Messaging API docs.
    - Use `body` exactly as received. Don't decode it, strip it, normalize line endings or Unicode,
      or JSON-parse and re-serialize it first; any of those changes the bytes LINE signed.
    - The secret and the header are str; HMAC works on bytes. Pick the encodings deliberately.
    - Compare with hmac.compare_digest, never ==, so the time taken doesn't reveal how many leading
      characters matched. Note what compare_digest accepts: bytes, or str with ASCII characters only.
    - A missing (None) or empty signature, or an empty channel secret, is False, not an exception.
    - Nothing a client sends may raise here: a malformed or non-ASCII header value is just False.
    - Return a real bool.
    """
    raise NotImplementedError("verify_signature: see the TODO above")
