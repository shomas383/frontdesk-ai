import pytest

from src.eval import build_parser
from src.lexical import BM25, tokenize
from src.retrieval import CANDIDATES, RRF_K, retrieval_config, rrf_fuse


def test_tokenize_keeps_content_words_and_normalizes():
    tokens = tokenize("お着替えは必要ですか？")
    assert "着替え" in tokens and "必要" in tokens
    assert not {"は", "です", "か", "御", "?"} & set(tokens)
    assert "子供" in tokenize("子どもも施術を受けられますか？")  # 子ども → 子供
    assert tokenize("PayPayは使えますか？")[0] == "paypay"


def test_tokenize_drops_light_verbs():
    assert "為る" not in tokenize("ボキボキされるのが怖い")


def test_bm25_ranks_matching_docs_and_skips_zero_scores():
    docs = [["駐車場", "台"], ["支払い", "現金", "カード"], ["カード", "回数券"], ["営業", "時間"]]
    bm25 = BM25(docs)
    assert bm25.top(["カード", "現金"], 5) == [1, 2]
    assert bm25.top(["存在しない"], 5) == []


def test_bm25_rarer_term_weighs_more():
    bm25 = BM25([["a", "common"], ["b", "common"], ["c", "rare"]])
    scores = bm25.scores(["common", "rare"])
    assert scores[2] > scores[0] == pytest.approx(scores[1])


def test_rrf_fuse_rewards_agreement():
    fused = rrf_fuse([["a", "b", "c"], ["c", "d"]], k=60)
    ids = [cid for cid, _ in fused]
    assert ids[0] == "c"  # rank 3 + rank 1 beats a single rank 1
    assert dict(fused)["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_fuse_ties_break_by_dense_rank_then_id():
    # a (dense 1) and x (keyword 1) tie; dense wins. y/z only in keyword at the same rank can't tie.
    ids = [cid for cid, _ in rrf_fuse([["a", "b"], ["x", "b"]])]
    assert ids == ["b", "a", "x"]


def test_rrf_fuse_with_empty_keyword_ranking_is_dense_order():
    assert [cid for cid, _ in rrf_fuse([["a", "b", "c"], []])] == ["a", "b", "c"]


def test_retrieval_config_and_flag():
    assert retrieval_config("dense") == {"retrieval": "dense"}
    assert retrieval_config("hybrid")["candidates"] == CANDIDATES
    assert retrieval_config("hybrid")["rrf_k"] == RRF_K
    assert build_parser().parse_args(["--name", "x"]).retrieval == "dense"
    assert build_parser().parse_args(["--name", "x", "--retrieval", "hybrid"]).retrieval == "hybrid"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--name", "x", "--retrieval", "bogus"])
