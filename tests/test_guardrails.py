from src.config import DEFAULT_TENANT
from src.db import RetrievedChunk
from src.doc_meta import load_doc_meta
from src.guardrails import HANDOFF_SENTENCE, asserts_current_unavailability, check_unstated_negative

TODAY = "2026-10-24"
META = load_doc_meta(DEFAULT_TENANT)


def _chunks(*ids):
    return [RetrievedChunk(cid, cid.split("#")[0], cid.split("#")[1], "", 0.5) for cid in ids]


# Real answers that were hand-labeled incorrect (stale-v2 r3)
Q55 = ("お友だち紹介キャンペーンは2025年12月31日をもって終了しております。現在は紹介割引の案内はございません。"
       "詳しい内容については、スタッフが確認してご連絡いたします。")
Q36 = ("2025年の年末に限定して販売していた全身整体60分のご利用券は、キャンペーンが終了しており、現在は販売しておりません。"
       "ギフト券の現在の取り扱いについては、スタッフが確認のうえご連絡いたします。")


def test_detects_current_unavailability_claims():
    assert asserts_current_unavailability("現在は紹介割引の案内はございません。")
    assert asserts_current_unavailability("現在は取り扱いがございません。")
    assert asserts_current_unavailability("現在はご購入いただけません。")
    assert not asserts_current_unavailability("現在は終了しております。")  # the dated fact itself is fine
    assert not asserts_current_unavailability("ご予約のない方の施術はお受けしておりません。")  # no "currently"
    assert not asserts_current_unavailability("現在ギフト券のご用意についての情報は確認できておりません。")


def test_fires_on_campaign_only_support_and_rewrites_to_handoff():
    res = check_unstated_negative(Q55, "handoff", ["10_campaign_2025"],
                                  _chunks("10_campaign_2025#お友だち紹介キャンペーン"), META, TODAY)
    assert res.fired
    assert "ございません" not in res.answer
    assert res.answer.startswith("お友だち紹介キャンペーンは2025年12月31日をもって終了しております。")
    assert HANDOFF_SENTENCE not in res.answer  # the answer already hands off
    assert res.removed == ["現在は紹介割引の案内はございません。"]


def test_answer_behavior_becomes_handoff_and_handoff_sentence_is_added():
    res = check_unstated_negative("年末ギフトは現在はご購入いただけません。", "answer", ["10_campaign_2025"],
                                  _chunks("10_campaign_2025#年末ギフトのご案内"), META, TODAY)
    assert res.fired and res.behavior == "handoff"
    assert res.answer == HANDOFF_SENTENCE


def test_drops_the_whole_sentence_when_the_claim_shares_it():
    res = check_unstated_negative(Q36, "handoff", ["10_campaign_2025"],
                                  _chunks("10_campaign_2025#年末ギフトのご案内"), META, TODAY)
    assert res.fired
    assert res.answer == "ギフト券の現在の取り扱いについては、スタッフが確認のうえご連絡いたします。"


def test_official_or_current_sources_suppress_the_guardrail():
    q53 = "ブログに掲載の6,600円は改定前の料金のため、現在はご利用いただけません。"
    res = check_unstated_negative(q53, "answer", ["01_menu_prices", "07_news"],
                                  _chunks("01_menu_prices#コース料金", "07_news#2026年3月1日　料金改定のお知らせ"),
                                  META, TODAY)
    assert not res.fired and res.answer == q53
    # an in-effect news section counts as current support; an expired one doesn't
    upcoming = _chunks("07_news#2026年10月15日　臨時休業のお知らせ")
    assert not check_unstated_negative("現在はご予約いただけません。", "answer", ["07_news"], upcoming, META, TODAY).fired
    expired = _chunks("07_news#2025年7月20日　夏季休業のお知らせ")
    assert check_unstated_negative("現在はご予約いただけません。", "answer", ["07_news"], expired, META, TODAY).fired


def test_blog_only_and_no_sources_fire_but_unknown_docs_and_no_meta_do_not():
    blog = _chunks("08_blog_katakori#当院でのケアについて")
    assert check_unstated_negative("現在は空きがございません。", "answer", ["08_blog_katakori"], blog, META, TODAY).fired
    assert check_unstated_negative("現在は空きがございません。", "answer", [], blog, META, TODAY).fired
    assert not check_unstated_negative("現在は空きがございません。", "answer", ["99_unknown"], blog, META, TODAY).fired
    assert not check_unstated_negative("現在は空きがございません。", "answer", [], blog, {}, TODAY).fired


def test_refuse_redirect_behavior_is_kept():
    res = check_unstated_negative("現在は対応しておりません。", "refuse_redirect", [], [], META, TODAY)
    assert res.fired and res.behavior == "refuse_redirect"
