"""Issue #25: 検索スコア閾値判定・隣接チャンク展開のテスト（外部サービスはすべてモック）。

対象フラグ:
- SCORE_THRESHOLD_ENABLED : _retrieve がスコア閾値未満のチャンクを除外する
- ADJACENT_CHUNK_ENABLED  : _retrieve がヒットチャンクの前後（chunk_index ±window）を追加取得する
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from webui import server
from workers import config

_client = TestClient(server.app)


def _make_chunk(book_id: str, chunk_index: int, score: float = 0.9, text: str = "text") -> dict:
    return {
        "book_id": book_id,
        "chunk_index": chunk_index,
        "text": text,
        "title": "T",
        "author": "A",
        "chapter": "C",
        "section": None,
        "page": 1,
        "score": score,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PgVectorStore.get_by_indices
# ─────────────────────────────────────────────────────────────────────────────


def test_pgvector_get_by_indices_returns_matching_chunks():
    from workers.embed.pgvector_store import PgVectorStore

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.return_value = [_make_chunk("b1", 3)]

    with patch("workers.embed.pgvector_store.psycopg.connect", return_value=mock_conn):
        store = PgVectorStore("dsn://fake")
        result = store.get_by_indices("b1", [3])

    sql = mock_cur.execute.call_args[0][0]
    params = mock_cur.execute.call_args[0][1]
    assert "chunk_index = ANY" in sql
    assert params["book_id"] == "b1"
    assert params["indices"] == [3]
    assert result == [_make_chunk("b1", 3)]


def test_pgvector_get_by_indices_empty_list_skips_query():
    from workers.embed.pgvector_store import PgVectorStore

    mock_conn = MagicMock()

    with patch("workers.embed.pgvector_store.psycopg.connect", return_value=mock_conn):
        store = PgVectorStore("dsn://fake")
        result = store.get_by_indices("b1", [])

    mock_conn.cursor.assert_not_called()
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# server._expand_adjacent_chunks
# ─────────────────────────────────────────────────────────────────────────────


def test_expand_adjacent_chunks_fetches_neighbors():
    hits = [_make_chunk("b", 5)]
    fake_store = MagicMock()
    fake_store.get_by_indices.return_value = [_make_chunk("b", 4), _make_chunk("b", 6)]

    result = server._expand_adjacent_chunks(hits, fake_store, window=1)

    fake_store.get_by_indices.assert_called_once_with("b", [4, 6])
    indices = [c["chunk_index"] for c in result]
    assert indices == [4, 5, 6]


def test_expand_adjacent_chunks_skips_already_present():
    """隣接チャンクが既にヒット結果に含まれる場合は再取得しない。"""
    hits = [_make_chunk("b", 5), _make_chunk("b", 6)]
    fake_store = MagicMock()
    fake_store.get_by_indices.return_value = [_make_chunk("b", 4), _make_chunk("b", 7)]

    result = server._expand_adjacent_chunks(hits, fake_store, window=1)

    fake_store.get_by_indices.assert_called_once_with("b", [4, 7])
    indices = sorted(c["chunk_index"] for c in result)
    assert indices == [4, 5, 6, 7]


def test_expand_adjacent_chunks_respects_window():
    hits = [_make_chunk("b", 10)]
    fake_store = MagicMock()
    fake_store.get_by_indices.return_value = []

    server._expand_adjacent_chunks(hits, fake_store, window=2)

    fake_store.get_by_indices.assert_called_once_with("b", [8, 9, 11, 12])


def test_expand_adjacent_chunks_no_negative_index():
    hits = [_make_chunk("b", 0)]
    fake_store = MagicMock()
    fake_store.get_by_indices.return_value = [_make_chunk("b", 1)]

    server._expand_adjacent_chunks(hits, fake_store, window=1)

    fake_store.get_by_indices.assert_called_once_with("b", [1])


def test_expand_adjacent_chunks_multi_book_grouped_per_book():
    hits = [_make_chunk("b1", 5), _make_chunk("b2", 2)]
    fake_store = MagicMock()
    fake_store.get_by_indices.side_effect = lambda book_id, indices: (
        [_make_chunk(book_id, i) for i in indices]
    )

    result = server._expand_adjacent_chunks(hits, fake_store, window=1)

    assert fake_store.get_by_indices.call_count == 2
    ids = {(c["book_id"], c["chunk_index"]) for c in result}
    assert ("b1", 4) in ids
    assert ("b1", 6) in ids
    assert ("b2", 1) in ids
    assert ("b2", 3) in ids


# ─────────────────────────────────────────────────────────────────────────────
# _retrieve: SCORE_THRESHOLD_ENABLED
# ─────────────────────────────────────────────────────────────────────────────


def test_retrieve_score_threshold_filters_low_score(monkeypatch):
    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", True)
    monkeypatch.setattr(config, "SCORE_THRESHOLD", 0.5)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    chunks = [_make_chunk("b", 0, score=0.9), _make_chunk("b", 1, score=0.3)]
    fake_store = MagicMock()
    fake_store.search.return_value = chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("query", top_k=5)

    assert [c["chunk_index"] for c in result] == [0]


def test_retrieve_score_threshold_disabled_keeps_all(monkeypatch):
    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", False)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    chunks = [_make_chunk("b", 0, score=0.9), _make_chunk("b", 1, score=0.01)]
    fake_store = MagicMock()
    fake_store.search.return_value = chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("query", top_k=5)

    assert len(result) == 2


def test_retrieve_score_threshold_applied_before_hybrid_keeps_keyword_only_hit(monkeypatch):
    """HYBRID_ENABLED 併用時、閾値判定はベクトル検索直後に適用され、
    キーワードのみでヒットしたチャンク（bigm スコア）は閾値の影響を受けない。
    """
    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", True)
    monkeypatch.setattr(config, "SCORE_THRESHOLD", 0.5)
    monkeypatch.setattr(config, "HYBRID_ENABLED", True)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    # ベクトル検索の結果は閾値未満（除外される）
    vec_chunks = [_make_chunk("b", 0, score=0.1)]
    # キーワード検索のみでヒットしたチャンク（bigm スコアは低い値になりがちだが除外対象ではない）
    kw_chunks = [_make_chunk("b", 1, score=0.1, text="keyword hit")]

    fake_store = MagicMock()
    fake_store.search.return_value = vec_chunks
    fake_store.search_keyword.return_value = kw_chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("query", top_k=5)

    # vec_chunks は閾値未満で事前に除外されているが、kw のみのヒットは生き残る
    assert [c["chunk_index"] for c in result] == [1]


def test_retrieve_score_threshold_applied_before_rerank_keeps_rerank_pick(monkeypatch):
    """RERANK_ENABLED 併用時、閾値判定はベクトル検索直後に適用され、
    Rerank 後の並び替え結果が古いベクトルスコアで再度除外されることはない。
    """
    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", True)
    monkeypatch.setattr(config, "SCORE_THRESHOLD", 0.5)
    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(config, "RERANK_CANDIDATE_K", 20)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    # どちらも閾値(0.5)以上でベクトル検索段階を通過する
    candidates = [_make_chunk("b", 0, score=0.9), _make_chunk("b", 1, score=0.55)]
    fake_store = MagicMock()
    fake_store.search.return_value = candidates

    # Rerank はベクトルスコアが低い方(chunk 1)を最有力と判定
    fake_reranker = MagicMock()
    fake_reranker.rerank.return_value = [candidates[1]]

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
        patch("workers.rerank.SentenceReranker", return_value=fake_reranker),
    ):
        result = server._retrieve("query", top_k=1)

    # Rerank の選択結果が閾値判定で再度除外されず、そのまま返る
    assert [c["chunk_index"] for c in result] == [1]


# ─────────────────────────────────────────────────────────────────────────────
# _retrieve: ADJACENT_CHUNK_ENABLED
# ─────────────────────────────────────────────────────────────────────────────


def test_retrieve_adjacent_chunk_enabled_calls_expand(monkeypatch):
    monkeypatch.setattr(config, "ADJACENT_CHUNK_ENABLED", True)
    monkeypatch.setattr(config, "ADJACENT_CHUNK_WINDOW", 1)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    chunks = [_make_chunk("b", 5)]
    fake_store = MagicMock()
    fake_store.search.return_value = chunks
    fake_store.get_by_indices.return_value = [_make_chunk("b", 6)]

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("query", top_k=5)

    fake_store.get_by_indices.assert_called_once_with("b", [4, 6])
    assert len(result) == 2


def test_retrieve_adjacent_chunk_disabled_skips_expand(monkeypatch):
    monkeypatch.setattr(config, "ADJACENT_CHUNK_ENABLED", False)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    chunks = [_make_chunk("b", 5)]
    fake_store = MagicMock()
    fake_store.search.return_value = chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("query", top_k=5)

    fake_store.get_by_indices.assert_not_called()
    assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# /api/chat: SCORE_THRESHOLD_ENABLED による「該当情報なし」メッセージ
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_no_results_message_when_threshold_filters_all(monkeypatch):
    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", True)
    monkeypatch.setattr(server, "_retrieve", lambda query, top_k, book_id=None: [])

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("LLM should not be called when no chunks pass threshold")

    monkeypatch.setattr("httpx.AsyncClient", _fail_if_called)

    res = _client.post("/api/chat", json={"query": "質問"})
    assert res.status_code == 200

    events = [
        __import__("json").loads(line[6:])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens
    assert any("見つかりません" in t for t in tokens)
    assert any(e["type"] == "done" for e in events)


def test_chat_normal_flow_when_threshold_disabled_and_no_chunks(monkeypatch):
    """SCORE_THRESHOLD_ENABLED=false のときは chunks=[] でも通常通り LLM を呼ぶ（既存挙動維持）。"""
    import json as _json
    from collections.abc import AsyncIterator

    monkeypatch.setattr(config, "SCORE_THRESHOLD_ENABLED", False)
    monkeypatch.setattr(server, "_retrieve", lambda query, top_k, book_id=None: [])

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_lines(self) -> AsyncIterator[str]:
            yield _json.dumps({"done": True})

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, *args, **kwargs):
            return _Stream()

    monkeypatch.setattr("httpx.AsyncClient", _Client)

    res = _client.post("/api/chat", json={"query": "質問"})
    assert res.status_code == 200
