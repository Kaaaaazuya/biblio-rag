"""Rerank 機能のユニットテスト（外部モデル不要・モック使用）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ──────────────────────────────────────────────
# workers/rerank/base.py
# ──────────────────────────────────────────────


def test_reranker_is_abstract():
    from workers.rerank.base import Reranker

    with pytest.raises(TypeError):
        Reranker()  # type: ignore[abstract]


def test_reranker_has_rerank_method():
    from workers.rerank.base import Reranker

    assert hasattr(Reranker, "rerank")


# ──────────────────────────────────────────────
# workers/rerank/sentence_reranker.py
# ──────────────────────────────────────────────


@patch("workers.rerank.sentence_reranker.CrossEncoder")
def test_sentence_reranker_returns_top_k(mock_ce_cls):
    """上位 top_k 件に絞って返す。"""
    from workers.rerank.sentence_reranker import SentenceReranker

    mock_model = MagicMock()
    mock_model.predict.return_value = [0.9, 0.3, 0.7, 0.1, 0.5]
    mock_ce_cls.return_value = mock_model

    candidates = [{"text": f"chunk {i}", "score": 0.5} for i in range(5)]
    reranker = SentenceReranker("test-reranker-model")
    results = reranker.rerank("クエリ", candidates, top_k=3)

    assert len(results) == 3
    # スコア降順になっているか
    scores = [r["rerank_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@patch("workers.rerank.sentence_reranker.CrossEncoder")
def test_sentence_reranker_attaches_rerank_score(mock_ce_cls):
    """結果に rerank_score が付与される。"""
    from workers.rerank.sentence_reranker import SentenceReranker

    mock_model = MagicMock()
    mock_model.predict.return_value = [0.8, 0.2]
    mock_ce_cls.return_value = mock_model

    candidates = [{"text": "a", "score": 0.5}, {"text": "b", "score": 0.4}]
    reranker = SentenceReranker("test-reranker-model")
    results = reranker.rerank("q", candidates, top_k=2)

    assert all("rerank_score" in r for r in results)
    assert results[0]["rerank_score"] == pytest.approx(0.8)
    assert results[1]["rerank_score"] == pytest.approx(0.2)


@patch("workers.rerank.sentence_reranker.CrossEncoder")
def test_sentence_reranker_empty_candidates(mock_ce_cls):
    """候補が空のとき空リストを返す（エラーなし）。"""
    from workers.rerank.sentence_reranker import SentenceReranker

    mock_model = MagicMock()
    mock_model.predict.return_value = []
    mock_ce_cls.return_value = mock_model

    reranker = SentenceReranker("test-reranker-model")
    results = reranker.rerank("q", [], top_k=5)

    assert results == []


@patch("workers.rerank.sentence_reranker.CrossEncoder")
def test_sentence_reranker_top_k_gt_candidates(mock_ce_cls):
    """top_k が候補数より多くても候補数だけ返す。"""
    from workers.rerank.sentence_reranker import SentenceReranker

    mock_model = MagicMock()
    mock_model.predict.return_value = [0.6, 0.9]
    mock_ce_cls.return_value = mock_model

    candidates = [{"text": "a", "score": 0.5}, {"text": "b", "score": 0.4}]
    reranker = SentenceReranker("test-reranker-model")
    results = reranker.rerank("q", candidates, top_k=10)

    assert len(results) == 2


# ──────────────────────────────────────────────
# config フラグ
# ──────────────────────────────────────────────


def test_config_rerank_flags_default_false(monkeypatch):
    """デフォルトは RERANK_ENABLED=false。"""
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    monkeypatch.delenv("RERANK_CANDIDATE_K", raising=False)

    import importlib

    import workers.config as cfg

    importlib.reload(cfg)

    assert cfg.RERANK_ENABLED is False
    assert cfg.RERANK_CANDIDATE_K == 20


def test_config_rerank_flags_enabled(monkeypatch):
    """RERANK_ENABLED=true で True になる。"""
    monkeypatch.setenv("RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_CANDIDATE_K", "30")

    import importlib

    import workers.config as cfg

    importlib.reload(cfg)

    assert cfg.RERANK_ENABLED is True
    assert cfg.RERANK_CANDIDATE_K == 30


# ──────────────────────────────────────────────
# webui/server.py の _retrieve 統合
# ──────────────────────────────────────────────


@patch("workers.rerank.sentence_reranker.CrossEncoder")
def test_retrieve_applies_rerank_when_enabled(mock_ce_cls, monkeypatch):
    """RERANK_ENABLED=True のとき _retrieve が reranker を呼ぶ。"""
    monkeypatch.setenv("RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_CANDIDATE_K", "10")

    import importlib

    import workers.config as cfg

    importlib.reload(cfg)

    mock_model = MagicMock()
    mock_model.predict.return_value = [float(i) / 10 for i in range(10)]
    mock_ce_cls.return_value = mock_model

    fake_chunks = [
        {"text": f"chunk {i}", "score": 0.5, "chapter": None, "section": None} for i in range(10)
    ]

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]

    mock_store = MagicMock()
    mock_store.search.return_value = fake_chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=mock_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=mock_store),
        patch("webui.server.config", cfg),
        patch("webui.server._reranker", None),  # シングルトンをリセット
    ):
        import webui.server as server

        result = server._retrieve("クエリ", top_k=5)

    assert len(result) == 5


def test_retrieve_skips_rerank_when_disabled(monkeypatch):
    """RERANK_ENABLED=False のとき reranker を呼ばない。"""
    monkeypatch.setenv("RERANK_ENABLED", "false")

    import importlib

    import workers.config as cfg

    importlib.reload(cfg)

    fake_chunks = [{"text": f"chunk {i}", "score": 0.5} for i in range(5)]

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]

    mock_store = MagicMock()
    mock_store.search.return_value = fake_chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=mock_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=mock_store),
        patch("webui.server.config", cfg),
    ):
        import webui.server as server

        result = server._retrieve("クエリ", top_k=5)

    assert result == fake_chunks
