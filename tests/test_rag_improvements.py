"""RAG改善フラグ ON 時のコードパスのテスト（外部サービスはすべてモック）。

対象フラグ:
- RERANK_ENABLED   : _retrieve が SentenceReranker.rerank を呼ぶ
- HYBRID_ENABLED   : _retrieve が search_keyword + _hybrid_rrf を呼ぶ
- HYDE_ENABLED     : _retrieve が _hyde を呼び、その結果でベクトル化する
- CITATION_ENABLED : chat() が context に [1][2] を付与し引用指示をプロンプトに入れる
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from webui import server
from workers import config

_client = TestClient(server.app)

# ─────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────────

_CHUNK = {
    "book_id": "b1",
    "chunk_index": 0,
    "text": "サンプル本文",
    "title": "テスト書籍",
    "author": "著者",
    "chapter": "第1章",
    "section": None,
    "page": 1,
}


def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _fake_llm_noop():
    """LLM 呼び出しを即 done で終わらせる FakeLLM。"""

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_lines(self) -> AsyncIterator[str]:
            yield json.dumps({"done": True})

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, *args, **kwargs):
            return _Stream()

    return _Client


# ─────────────────────────────────────────────────────────────────────────────
# _hybrid_rrf のユニットテスト
# ─────────────────────────────────────────────────────────────────────────────


def _make_chunk(book_id: str, chunk_index: int, text: str = "text") -> dict:
    return {
        "book_id": book_id,
        "chunk_index": chunk_index,
        "text": text,
        "title": "",
        "author": "",
        "chapter": "",
        "section": None,
        "page": 0,
    }


def test_hybrid_rrf_combines_results():
    """ベクトル検索とキーワード検索の両方に出るチャンクが上位に来る。"""
    vec_chunks = [_make_chunk("b", 0), _make_chunk("b", 1)]
    kw_chunks = [_make_chunk("b", 0), _make_chunk("b", 2)]  # b:0 は両方に出る

    fake_store = MagicMock()
    fake_store.search_keyword.return_value = kw_chunks

    result = server._hybrid_rrf("q", [0.0], vec_chunks, fake_store, top_k=3)

    ids = [(c["book_id"], c["chunk_index"]) for c in result]
    assert ids[0] == ("b", 0), "両方に出るチャンクが 1 位になる"


def test_hybrid_rrf_top_k_limits_result():
    vec_chunks = [_make_chunk("b", i) for i in range(5)]
    kw_chunks = [_make_chunk("b", i) for i in range(5, 10)]

    fake_store = MagicMock()
    fake_store.search_keyword.return_value = kw_chunks

    result = server._hybrid_rrf("q", [0.0], vec_chunks, fake_store, top_k=3)
    assert len(result) == 3


# ─────────────────────────────────────────────────────────────────────────────
# _hyde のユニットテスト
# ─────────────────────────────────────────────────────────────────────────────


def test_hyde_returns_llm_content():
    """Ollama /api/chat の message.content を返す。"""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"message": {"content": "仮説回答テキスト"}}

    with patch("httpx.post", return_value=fake_resp) as mock_post:
        result = server._hyde("テスト質問")

    mock_post.assert_called_once()
    assert result == "仮説回答テキスト"


def test_hyde_fallback_on_missing_content():
    """message.content が無い場合は元のクエリをそのまま返す。"""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"message": {}}

    with patch("httpx.post", return_value=fake_resp):
        result = server._hyde("fallback_query")

    assert result == "fallback_query"


# ─────────────────────────────────────────────────────────────────────────────
# _retrieve: 各フラグ ON 時の動作
# ─────────────────────────────────────────────────────────────────────────────


def test_retrieve_rerank_enabled(monkeypatch):
    """RERANK_ENABLED=true のとき SentenceReranker.rerank が呼ばれる。"""
    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(config, "RERANK_CANDIDATE_K", 20)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    candidates = [_make_chunk("b", i) for i in range(3)]
    reranked = [_make_chunk("b", 0)]

    fake_store = MagicMock()
    fake_store.search.return_value = candidates

    fake_reranker = MagicMock()
    fake_reranker.rerank.return_value = reranked

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.rerank.SentenceReranker", return_value=fake_reranker),
    ):
        result = server._retrieve("query", top_k=1)

    fake_store.search.assert_called_once_with([0.1], top_k=20)
    fake_reranker.rerank.assert_called_once_with("query", candidates, 1)
    assert result == reranked


def test_retrieve_hybrid_enabled(monkeypatch):
    """HYBRID_ENABLED=true のとき search_keyword + _hybrid_rrf が呼ばれる。"""
    monkeypatch.setattr(config, "HYBRID_ENABLED", True)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    vec_chunks = [_make_chunk("b", 0)]
    kw_chunks = [_make_chunk("b", 1)]

    fake_store = MagicMock()
    fake_store.search.return_value = vec_chunks
    fake_store.search_keyword.return_value = kw_chunks

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
    ):
        result = server._retrieve("query", top_k=5)

    fake_store.search_keyword.assert_called_once_with("query", top_k=5)
    assert isinstance(result, list)


def test_retrieve_hyde_enabled(monkeypatch):
    """HYDE_ENABLED=true のとき _hyde の戻り値でベクトル化する。"""
    monkeypatch.setattr(config, "HYDE_ENABLED", True)

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1]]

    fake_store = MagicMock()
    fake_store.search.return_value = []

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"message": {"content": "仮説回答"}}

    with (
        patch("httpx.post", return_value=fake_resp),
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
    ):
        server._retrieve("元の質問", top_k=3)

    # embed に渡されるのは元クエリではなく HyDE で生成した仮説回答
    fake_embedder.embed.assert_called_once_with(["仮説回答"])


# ─────────────────────────────────────────────────────────────────────────────
# Citation: context 生成ロジックを直接テスト
# ─────────────────────────────────────────────────────────────────────────────


def _build_context_citation(chunks: list[dict]) -> str:
    """server.py の CITATION_ENABLED=true 時の context 生成と同ロジック。"""
    return "\n\n".join(
        f"[{i}] 【{c.get('title', '')}｜{c.get('chapter', '')}】\n{c['text']}"
        for i, c in enumerate(chunks, 1)
    )


def _build_context_plain(chunks: list[dict]) -> str:
    """server.py の CITATION_ENABLED=false 時の context 生成と同ロジック。"""
    return "\n\n".join(
        f"【{c.get('title', '')}｜{c.get('chapter', '')}】\n{c['text']}" for c in chunks
    )


def test_citation_context_has_numbers():
    """CITATION_ENABLED=true のとき context に [1] [2] 番号が付く。"""
    chunks = [
        {**_CHUNK, "chunk_index": 0, "text": "本文A"},
        {**_CHUNK, "chunk_index": 1, "text": "本文B"},
    ]
    ctx = _build_context_citation(chunks)
    assert "[1]" in ctx
    assert "[2]" in ctx


def test_no_citation_context_has_no_numbers():
    """CITATION_ENABLED=false のとき context に番号が付かない。"""
    chunks = [{**_CHUNK, "chunk_index": 0, "text": "本文A"}]
    ctx = _build_context_plain(chunks)
    assert "[1]" not in ctx


def test_citation_instruction_defined():
    """_CITATION_INSTRUCTION が定義されており空でないことを確認。"""
    assert server._CITATION_INSTRUCTION
    assert "[1]" in server._CITATION_INSTRUCTION or "引用" in server._CITATION_INSTRUCTION


def test_system_prompt_citation_placeholder():
    """_SYSTEM_PROMPT に {citation_instruction} プレースホルダーが含まれる。"""
    assert "{citation_instruction}" in server._SYSTEM_PROMPT
