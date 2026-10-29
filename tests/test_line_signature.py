"""Spec for src.line_signature.verify_signature.

The expected signatures are fixed values, computed once outside the tests, so the tests state the
contract without containing an implementation.
"""

import json
import unicodedata

import pytest

import src.line_signature
from src.line_signature import verify_signature

SECRET = "8f2b1c0d9e7a6b5c4d3e2f1a0b9c8d7e"
BODY = (b'{"destination":"Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx","events":[{"type":"message","message":'
        b'{"type":"text","id":"1","text":"' + "60分の料金は？".encode("utf-8") + b'"}}]}\n')
SIG = "foTnJV7f/1oaSIsxpLQP140GKc/Dg4jR/ED8pJqufgw="

BODY_CRLF = BODY.replace(b"\n", b"\r\n")
SIG_CRLF = "vur7nf7JLgFhSrJK7r4GBNfrE/XGOz9jGXWys6Qp7D8="

JA_TEXT = '{"events":[{"type":"message","message":{"type":"text","text":"ガイドを見ました😊 「予約」できますか？"}}]}'
BODY_JA = JA_TEXT.encode("utf-8")
SIG_JA = "ZVNjFLB8z7fBbAi7L9X+eKuU0rThRt0E15Yd5dfBB4Y="

SIG_EMPTY_SECRET = "uQS7t/2ta8hKxuL2iSgfl6NCpO4RwSrXBNKoeE9Wkks="  # BODY signed with ""
SIG_OTHER_SECRET = "0y1j/I/3Kfc2V5BbVieWebOS65j+U+xWUxOuLkjhxto="  # BODY signed with "another-secret"


def test_fixture_text_is_nfc():
    # Guards the fixture itself: SIG_JA was computed over the NFC form of JA_TEXT.
    assert unicodedata.is_normalized("NFC", JA_TEXT)


def test_valid_signature():
    assert verify_signature(SECRET, BODY, SIG) is True


def test_returns_a_real_bool_when_invalid():
    assert verify_signature(SECRET, BODY, SIG_CRLF) is False


@pytest.mark.parametrize("signature", [None, ""])
def test_missing_signature(signature):
    assert verify_signature(SECRET, BODY, signature) is False


def test_signature_from_another_secret():
    assert verify_signature(SECRET, BODY, SIG_OTHER_SECRET) is False
    assert verify_signature("another-secret", BODY, SIG) is False


def test_empty_secret_is_rejected_even_with_a_matching_signature():
    # A missing LINE_CHANNEL_SECRET must never turn into "anything signed with an empty key is fine".
    assert verify_signature("", BODY, SIG_EMPTY_SECRET) is False


@pytest.mark.parametrize("body", [
    BODY[:-1],                                   # last byte (the newline) dropped
    BODY + b" ",                                 # one byte added
    BODY.replace(b'"id":"1"', b'"id":"2"'),      # one byte changed
    b"",
])
def test_modified_body(body):
    assert verify_signature(SECRET, body, SIG) is False


def test_crlf_body_is_verified_as_raw_bytes():
    assert SIG_CRLF != SIG
    assert verify_signature(SECRET, BODY_CRLF, SIG_CRLF) is True
    assert verify_signature(SECRET, BODY_CRLF, SIG) is False   # no CRLF -> LF normalization
    assert verify_signature(SECRET, BODY, SIG_CRLF) is False


def test_emoji_and_japanese_body():
    assert verify_signature(SECRET, BODY_JA, SIG_JA) is True


def test_unicode_normalization_changes_the_bytes():
    nfd = unicodedata.normalize("NFD", JA_TEXT).encode("utf-8")  # ガ -> カ + combining dakuten
    assert nfd != BODY_JA
    assert verify_signature(SECRET, nfd, SIG_JA) is False


def test_reserialized_json_does_not_verify():
    # Parsing first and re-encoding gives different bytes (here: \u escapes), so it must fail.
    reserialized = json.dumps(json.loads(BODY_JA)).encode("utf-8")
    assert reserialized != BODY_JA
    assert verify_signature(SECRET, reserialized, SIG_JA) is False


@pytest.mark.parametrize("signature", [
    SIG.rstrip("="),               # padding stripped
    f" {SIG}",                     # whitespace around it
    f"{SIG}\n",
    SIG.lower(),                   # base64 is case-sensitive
    SIG[:-2] + "A=",               # last character changed
    "not base64 at all!",
    "署名" * 22,                    # non-ASCII header value
    SIG.replace("+", "-").replace("/", "_"),  # URL-safe alphabet is a different value
])
def test_malformed_or_altered_signature_is_false_without_raising(signature):
    assert verify_signature(SECRET, BODY, signature) is False


def test_uses_a_constant_time_comparison(monkeypatch):
    calls = []
    original = src.line_signature.hmac.compare_digest

    def spy(a, b):
        calls.append((type(a), type(b)))
        return original(a, b)

    monkeypatch.setattr(src.line_signature.hmac, "compare_digest", spy)
    assert verify_signature(SECRET, BODY, SIG) is True
    assert verify_signature(SECRET, BODY, SIG_CRLF) is False
    assert len(calls) == 2
