import pytest

from src.redact import EMAIL_TOKEN, PHONE_TOKEN, redact_pii


@pytest.mark.parametrize("phone", [
    "03-1234-5678", "090-1234-5678", "09012345678", "0312345678", "0120-123-456", "(03) 1234-5678",
    "+81 90 1234 5678", "+81-3-1234-5678", "０９０－１２３４－５６７８", "090ー1234ー5678", "080 1234 5678",
])
def test_phone_numbers_are_redacted(phone):
    assert redact_pii(f"電話は{phone}です。") == f"電話は{PHONE_TOKEN}です。"


@pytest.mark.parametrize("email", ["taro@example.com", "taro.yamada+clinic@mail.example.co.jp", "a_b@x.jp"])
def test_emails_are_redacted(email):
    assert redact_pii(f"連絡先は{email}まで") == f"連絡先は{EMAIL_TOKEN}まで"


@pytest.mark.parametrize("text", [
    "60分の全身整体は6,600円ですか？", "10:00-19:00は空いていますか", "2026-10-24に予約したい",
    "2026年10月24日", "初回は60分、2回目以降は30分", "会員番号12345", "123-4567（郵便番号）", "0.5回分",
])
def test_ordinary_numbers_are_kept(text):
    assert redact_pii(text) == text


def test_mixed():
    text = "090-1111-2222かa@b.jpへ。料金は6,600円、10/24 10:00希望"
    assert redact_pii(text) == f"{PHONE_TOKEN}か{EMAIL_TOKEN}へ。料金は6,600円、10/24 10:00希望"
