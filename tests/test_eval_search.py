"""eval_search.py のユニットテスト（DB・Ollama 不要）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.eval_search import _first_relevant_rank, _relevant, evaluate


@pytest.fixture(autouse=True)
def disable_rag_extensions(monkeypatch):
    monkeypatch.setattr("scripts.eval_search.config.HYDE_ENABLED", False)
    monkeypatch.setattr("scripts.eval_search.config.RERANK_ENABLED", False)


def _make_hit(text: str, book_id: str = "book1", chapter: str = "第一章") -> dict:
    return {"text": text, "book_id": book_id, "chapter": chapter}


# ──────────────────────────────────────────────
# _relevant
# ──────────────────────────────────────────────


def test_relevant_keyword_match():
    hit = _make_hit("ベクトルインデックスを構築する")
    assert _relevant(hit, {"any": ["ベクトル", "埋め込み"]}) is True


def test_relevant_keyword_no_match():
    hit = _make_hit("ヘッダを除去する")
    assert _relevant(hit, {"any": ["ベクトル", "埋め込み"]}) is False


def test_relevant_book_id_filter():
    hit = _make_hit("テスト本文", book_id="book2")
    # book_id が異なればキーワードが一致しても False
    assert _relevant(hit, {"book_id": "book1", "any": ["本文"]}) is False


def test_relevant_book_id_match():
    hit = _make_hit("テスト本文", book_id="book1")
    assert _relevant(hit, {"book_id": "book1", "any": ["本文"]}) is True


def test_relevant_chapter_match():
    hit = _make_hit("設計の前提について", chapter="第一章 設計の前提")
    assert _relevant(hit, {"chapter": "第一章"}) is True


def test_relevant_chapter_no_match():
    hit = _make_hit("設計の前提について", chapter="第一章 設計の前提")
    assert _relevant(hit, {"chapter": "第二章"}) is False


# ──────────────────────────────────────────────
# _first_relevant_rank
# ──────────────────────────────────────────────


def test_first_relevant_rank_found():
    hits = [
        _make_hit("無関係なテキスト"),
        _make_hit("ベクトル検索の説明"),
        _make_hit("他の内容"),
    ]
    assert _first_relevant_rank(hits, {"any": ["ベクトル"]}) == 2


def test_first_relevant_rank_not_found():
    hits = [_make_hit("無関係"), _make_hit("別の無関係")]
    assert _first_relevant_rank(hits, {"any": ["ベクトル"]}) is None


def test_first_relevant_rank_first_hit():
    hits = [_make_hit("ベクトルインデックス"), _make_hit("その他")]
    assert _first_relevant_rank(hits, {"any": ["ベクトル"]}) == 1


# ──────────────────────────────────────────────
# evaluate
# ──────────────────────────────────────────────


def _make_embedder(vec=None):
    m = MagicMock()
    m.embed.return_value = [vec or [0.1] * 1024]
    return m


def test_evaluate_all_hit():
    embedder = _make_embedder()
    store = MagicMock()
    store.search.return_value = [_make_hit("ベクトル検索の仕組み")]

    queries = [{"q": "ベクトル検索とは", "any": ["ベクトル"]}]
    rows, summary = evaluate(queries, embedder, store, top_k=5)

    assert summary["hit@k"] == 1.0
    assert summary["mrr"] == 1.0
    assert rows[0]["rank"] == 1


def test_evaluate_all_miss():
    embedder = _make_embedder()
    store = MagicMock()
    store.search.return_value = [_make_hit("無関係なテキスト")]

    queries = [{"q": "ベクトル検索とは", "any": ["ベクトル"]}]
    rows, summary = evaluate(queries, embedder, store, top_k=5)

    assert summary["hit@k"] == 0.0
    assert summary["mrr"] == 0.0
    assert rows[0]["rank"] is None


def test_evaluate_mrr_second_rank():
    embedder = _make_embedder()
    store = MagicMock()
    store.search.return_value = [
        _make_hit("無関係"),
        _make_hit("ベクトルインデックス構築"),
    ]

    queries = [{"q": "ベクトル検索とは", "any": ["ベクトル"]}]
    rows, summary = evaluate(queries, embedder, store, top_k=5)

    assert rows[0]["rank"] == 2
    assert abs(summary["mrr"] - 0.5) < 1e-9


def test_evaluate_empty_queries():
    embedder = _make_embedder()
    store = MagicMock()

    _, summary = evaluate([], embedder, store, top_k=5)

    assert summary["n"] == 0
    assert summary["hit@k"] == 0.0
    assert summary["mrr"] == 0.0
