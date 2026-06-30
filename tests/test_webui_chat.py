"""webui.server の RAG チャット機能のテスト（外部サービスをすべてモック）。

- _retrieve    : OllamaEmbedder / PgVectorStore をモック
- /api/chat    : _retrieve を差し替え + FakeLLM で Ollama HTTP を代替
- _run_pipeline: ObjectStore / extract / chunk / embed 各層をモック
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from webui import server
from workers.embed.base import Embedder

_client = TestClient(server.app)

# ─────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────────


def _sse_events(text: str) -> list[dict]:
    """SSE レスポンスのテキストから data: 行を抽出する。"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _fake_llm(lines: list[str]):
    """Ollama /api/chat SSE ストリームを偽装する httpx.AsyncClient 代替クラスを返す。"""

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_lines(self) -> AsyncIterator[str]:
            for line in lines:
                yield line

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


def _fake_retrieve(query: str, top_k: int) -> list[dict]:
    return [
        {
            "book_id": "book1",
            "text": "サンプル本文",
            "title": "テスト書籍",
            "author": "著者名",
            "chapter": "第1章",
            "section": None,
            "page": 1,
        }
    ]


class _FakeEmbedder(Embedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


# ─────────────────────────────────────────────────────────────────────────────
# _retrieve のテスト
# ─────────────────────────────────────────────────────────────────────────────


def test_retrieve_calls_embedder_and_store():
    fake_vec = [0.1, 0.2, 0.3]
    fake_results = [{"book_id": "b", "text": "t", "title": "T"}]

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [fake_vec]

    fake_store = MagicMock()
    fake_store.search.return_value = fake_results

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
    ):
        result = server._retrieve("my query", 3)

    fake_embedder.embed.assert_called_once_with(["my query"])
    fake_store.search.assert_called_once_with(fake_vec, top_k=3, book_id=None)
    fake_store.close.assert_called_once()
    assert result == fake_results


# ─────────────────────────────────────────────────────────────────────────────
# /api/chat SSE エンドポイントのテスト
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_requires_query():
    res = _client.post("/api/chat", json={"query": ""})
    assert res.status_code == 400


def test_chat_sse_sources_event(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm(
            [
                json.dumps({"message": {"content": "回答"}, "done": False}),
                json.dumps({"done": True}),
            ]
        ),
    )

    events = _sse_events(_client.post("/api/chat", json={"query": "質問"}).text)
    sources = [e for e in events if e["type"] == "sources"]
    assert len(sources) == 1
    assert sources[0]["sources"][0]["title"] == "テスト書籍"


def test_chat_sse_token_events(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm(
            [
                json.dumps({"message": {"content": "Hello"}, "done": False}),
                json.dumps({"message": {"content": " World"}, "done": False}),
                json.dumps({"done": True}),
            ]
        ),
    )

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hello", " World"]


def test_chat_sse_done_event(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr("httpx.AsyncClient", _fake_llm([json.dumps({"done": True})]))

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    assert any(e["type"] == "done" for e in events)


def test_chat_sse_ollama_error_event(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm([json.dumps({"error": "model not found"})]),
    )

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "model not found" in errors[0]["message"]


# ─────────────────────────────────────────────────────────────────────────────
# _run_pipeline のテスト
# ─────────────────────────────────────────────────────────────────────────────


def test_run_pipeline_success():
    server._status.clear()

    fake_obj_store = MagicMock()
    fake_obj_store.get_bytes.return_value = b"pdf-content"
    fake_obj_store.get_meta.return_value = {"title": "T", "author": "A"}

    with (
        patch("workers.storage.ObjectStore", return_value=fake_obj_store),
        patch("workers.extract.extract.extract_pdf_to_markdown", return_value="# md"),
        patch("workers.chunk.chunk.HeuristicChunker") as MockChunker,
        patch("workers.embed.pipeline.make_embedder", return_value=_FakeEmbedder()),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=MagicMock()),
    ):
        MockChunker.return_value.chunk.return_value = [
            {"book_id": "pipeline-test", "chunk_index": 0, "text": "本文"}
        ]
        server._run_pipeline("pipeline-test")

    assert server._status["pipeline-test"]["status"] == "done"


def test_run_pipeline_sets_failed_on_error():
    server._status.clear()

    with patch("workers.storage.ObjectStore", side_effect=RuntimeError("接続失敗")):
        server._run_pipeline("fail-test")

    assert server._status["fail-test"]["status"] == "failed"
    assert "接続失敗" in server._status["fail-test"]["error"]
