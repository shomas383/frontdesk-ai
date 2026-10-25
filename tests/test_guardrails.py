from src.config import DATA_DIR, DEFAULT_TENANT
from src.db import RetrievedChunk
from src.doc_meta import load_doc_meta
from src.guardrails import HANDOFF_SENTENCE, asserts_current_unavailability, check_unstated_negative, claim_subject

TODAY = "2026-10-24"
META = load_doc_meta(DEFAULT_TENANT)


def _chunks(*ids):
    return [RetrievedChunk(cid, cid.split("#")[0], cid.split("#")[1], "", 0.5) for cid in ids]


def _texts(*doc_ids):
    return {d: (DATA_DIR / DEFAULT_TENANT / "docs" / f"{d}.md").read_text(encoding="utf-8") for d in doc_ids}


def _check(answer, sources, chunk_ids, behavior="handoff"):
    return check_unstated_negative(answer, behavior, sources, _chunks(*chunk_ids), META, TODAY, _texts(*sources))


# Real answers that were hand-labeled incorrect
Q55 = ("お友だち紹介キャンペーンは2025年12月31日をもって終了しております。現在は紹介割引の案内はございません。"
       "詳しい内容については、スタッフが確認してご連絡いたします。")
Q36 = ("2025年の年末に限定して販売していた全身整体60分のご利用券は、キャンペーンが終了しており、現在は販売しておりません。"
       "ギフト券の現在の取り扱いについては、スタッフが確認のうえご連絡いたします。")
Q36_SMALLDOCS_R1 = ("ギフト券は現在ご用意しておりません。2025年の年末に限り、全身整体60分のご利用券を数量限定で販売していましたが、"
                    "そちらは終了しております。その他の点については、スタッフが確認のうえご連絡いたします。")
Q36_SMALLDOCS_R2 = ("ギフト券のご用意はございません。2025年の年末に限り販売していた全身整体60分のご利用券は、現在は終了しております。"
                    "ご不明な点はスタッフがご案内いたします。")


def test_detects_current_unavailability_claims():
    assert asserts_current_unavailability("現在は紹介割引の案内はございません。")
    assert asserts_current_unavailability("現在は取り扱いがございません。")
    assert asserts_current_unavailability("現在はご購入いただけません。")
    assert not asserts_current_unavailability("現在は終了しております。")  # the dated fact itself is fine
    assert not asserts_current_unavailability("ご予約のない方の施術はお受けしておりません。")  # no "currently"
    assert not asserts_current_unavailability("現在ギフト券のご用意についての情報は確認できておりません。")


def test_offering_negatives_are_caught_without_genzai():
    assert asserts_current_unavailability("ギフト券のご用意はございません。")
    assert asserts_current_unavailability("ギフト券の取り扱いはございません。")
    assert asserts_current_unavailability("ギフト券は取り扱っておりません。")


def test_not_in_documents_sentences_are_ignored():
    # q38 smalldocs r2: a false positive before this fix
    assert not asserts_current_unavailability("申し訳ございませんが、施術者の指名に関する情報は現在の資料に記載がございません。")
    assert not asserts_current_unavailability("ギフト券のご用意については資料に記載がございません。")


def test_claim_subject():
    assert claim_subject("ギフト券は現在ご用意しておりません。") == "ギフト券"
    assert claim_subject("ギフト券のご用意はございません。") == "ギフト券"
    assert claim_subject("現在は紹介割引の案内はございません。") == "紹介割引"
    assert claim_subject("ブログに掲載の6,600円は改定前の料金のため、現在はご利用いただけません。") == "6,600円"
    assert claim_subject("ご利用券（年末ギフト）は、現在はご購入いただけません。") == "年末ギフト"


def test_fires_on_campaign_only_support_and_rewrites_to_handoff():
    res = _check(Q55, ["10_campaign_2025"], ["10_campaign_2025#お友だち紹介キャンペーン"])
    assert res.fired
    assert "ございません" not in res.answer
    assert res.answer.startswith("お友だち紹介キャンペーンは2025年12月31日をもって終了しております。")
    assert HANDOFF_SENTENCE not in res.answer  # the answer already hands off
    assert res.removed == ["現在は紹介割引の案内はございません。"]


def test_official_doc_without_the_subject_does_not_excuse_the_claim():
    # q36 smalldocs r1: also cited the full price list, which never mentions gift tickets
    res = _check(Q36_SMALLDOCS_R1, ["10_campaign_2025", "01_menu_prices"],
                 ["10_campaign_2025#年末ギフトのご案内", "01_menu_prices#全文"])
    assert res.fired and res.removed == ["ギフト券は現在ご用意しておりません。"]
    assert res.answer.startswith("2025年の年末に限り")


def test_offering_negative_without_genzai_is_rewritten():
    # q36 smalldocs r2: behavior was "answer"
    res = _check(Q36_SMALLDOCS_R2, ["10_campaign_2025"], ["10_campaign_2025#年末ギフトのご案内"], behavior="answer")
    assert res.fired and res.behavior == "handoff"
    assert "ギフト券のご用意はございません" not in res.answer


def test_answer_behavior_becomes_handoff_and_handoff_sentence_is_added():
    res = _check("年末ギフトは現在はご購入いただけません。", ["10_campaign_2025"], ["10_campaign_2025#年末ギフトのご案内"],
                 behavior="answer")
    assert res.fired and res.behavior == "handoff"
    assert res.answer == HANDOFF_SENTENCE


def test_drops_the_whole_sentence_when_the_claim_shares_it():
    res = _check(Q36, ["10_campaign_2025"], ["10_campaign_2025#年末ギフトのご案内"])
    assert res.fired
    assert res.answer == "ギフト券の現在の取り扱いについては、スタッフが確認のうえご連絡いたします。"


def test_official_or_current_sources_that_mention_the_subject_suppress_the_guardrail():
    q53 = "ブログに掲載の6,600円は改定前の料金のため、現在はご利用いただけません。"
    res = _check(q53, ["01_menu_prices", "07_news"], ["01_menu_prices#コース料金", "07_news#2026年3月1日　料金改定のお知らせ"])
    assert not res.fired and res.answer == q53  # 6,600円 appears in the price-revision notice
    assert not _check("現在、回数券の取り扱いはございません。", ["01_menu_prices"], ["01_menu_prices#回数券"]).fired
    # an in-effect news section counts as current support; an expired one doesn't
    upcoming = ["07_news#2026年10月15日　臨時休業のお知らせ"]
    assert not _check("現在、臨時休業のため11月12日はご予約いただけません。", ["07_news"], upcoming).fired
    expired = ["07_news#2025年7月20日　夏季休業のお知らせ"]
    assert _check("夏季休業は現在はご予約いただけません。", ["07_news"], expired).fired


def test_blog_only_and_no_sources_fire_but_unknown_docs_and_no_meta_do_not():
    blog = _chunks("08_blog_katakori#当院でのケアについて")
    assert check_unstated_negative("現在は空きがございません。", "answer", ["08_blog_katakori"], blog, META, TODAY).fired
    assert check_unstated_negative("現在は空きがございません。", "answer", [], blog, META, TODAY).fired
    assert not check_unstated_negative("現在は空きがございません。", "answer", ["99_unknown"], blog, META, TODAY).fired
    assert not check_unstated_negative("現在は空きがございません。", "answer", [], blog, {}, TODAY).fired


def test_refuse_redirect_behavior_is_kept():
    res = check_unstated_negative("現在は対応しておりません。", "refuse_redirect", [], [], META, TODAY)
    assert res.fired and res.behavior == "refuse_redirect"


def test_restating_an_ended_campaign_is_not_a_claim():
    # labeled correct: q54 smalldocs r1, q55 stale-v2 r2
    assert not asserts_current_unavailability("夏の回数券キャンペーンは2025年8月31日に終了しており、現在は実施しておりません。")
    assert not asserts_current_unavailability("お友だち紹介キャンペーンは2025年12月31日をもって終了しており、現在は実施しておりません。")
    # labeled incorrect: product availability, even with 終了 (q36 stale-v2 r3, q56 rerank)
    assert asserts_current_unavailability("ご利用券は、キャンペーンが終了しており、現在は販売しておりません。")
    assert asserts_current_unavailability("ご利用券（ギフト）は、キャンペーン終了のため現在はご購入いただけません。")
