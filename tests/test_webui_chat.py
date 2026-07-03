"""webui.server の RAG チャット機能のテスト（ChatClient 抽象化後）。

- _retrieve    : OllamaEmbedder / PgVectorStore をモック
- /api/chat    : _retrieve を差し替え + MockChatClient で生成をモック
- _run_pipeline: ObjectStore / extract / chunk / embed 各層をモック
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from starlette.testclient import TestClient

from webui import server
from workers.chat.base import ChatClient
from workers.embed.base import Embedder

_client = TestClient(server.app)


def _sse_events(text: str) -> list[dict]:
    """SSE レスポンスのテキストから data: 行を抽出する。"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


class _MockChatClient(ChatClient):
    """テスト用モック ChatClient。設定可能なトークン列を返す。"""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens

    async def stream_chat(self, messages: list[dict], model: str | None = None):
        for token in self.tokens:
            yield token


def _fake_retrieve(query: str, top_k: int, book_id: str | None = None) -> list[dict]:
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


# /api/chat SSE エンドポイントのテスト


def test_chat_requires_query():
    res = _client.post("/api/chat", json={"query": ""})
    assert res.status_code == 400


def test_chat_rejects_non_string_book_id():
    """book_id が文字列以外（リスト等）のとき 400 を返す。"""
    res = _client.post("/api/chat", json={"query": "テスト", "book_id": ["invalid"]})
    assert res.status_code == 400
    assert "book_id" in res.json()["detail"]


def test_chat_rejects_malformed_json_body():
    """JSON として解釈できないリクエストボディを受けたとき 400 を返す。"""
    client = TestClient(server.app, raise_server_exceptions=False)
    res = client.post(
        "/api/chat",
        content=b"not valid json{{{",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400
    assert "detail" in res.json()


def test_chat_sse_sources_event(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(["回答"]))

    events = _sse_events(_client.post("/api/chat", json={"query": "質問"}).text)
    sources = [e for e in events if e["type"] == "sources"]
    assert len(sources) == 1
    assert sources[0]["sources"][0]["title"] == "テスト書籍"


def test_chat_sse_token_events(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(["Hello", " World"]))

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hello", " World"]


def test_chat_sse_done_event(monkeypatch):
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(["response"]))

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    assert any(e["type"] == "done" for e in events)


def test_chat_sse_chat_client_error(monkeypatch, caplog):
    """ChatClient からのエラーは詳細情報を返さず、汎用メッセージを返す。"""
    import logging

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    async def mock_error_stream(messages, model=None):
        raise RuntimeError("model not found")
        yield  # unreachable

    mock_client = AsyncMock()
    mock_client.stream_chat = mock_error_stream
    monkeypatch.setattr(server, "_make_chat_client", lambda: mock_client)

    with caplog.at_level(logging.ERROR):
        events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    # 内部情報（model not found）は返さない
    assert "model not found" not in errors[0]["message"]
    assert "An error occurred" in errors[0]["message"]


def test_chat_with_hyde_enabled(monkeypatch):
    """HYDE_ENABLED=true のとき、仮説回答が生成されて retrieval に渡されることを確認。"""
    monkeypatch.setattr(server.config, "HYDE_ENABLED", True)

    class FakeHyDEClient(ChatClient):
        async def stream_chat(self, messages, model=None):
            yield "仮説回答"

    def mock_make_chat_client(timeout=None):
        return FakeHyDEClient()

    monkeypatch.setattr(server, "_make_chat_client", mock_make_chat_client)

    retrieved_query = None

    def fake_retrieve(query, top_k, book_id=None):
        nonlocal retrieved_query
        retrieved_query = query
        return _fake_retrieve(query, top_k, book_id)

    monkeypatch.setattr(server, "_retrieve", fake_retrieve)

    _client.post("/api/chat", json={"query": "元の質問"})
    assert retrieved_query == "仮説回答"


def test_chat_with_hyde_failure_fallback(monkeypatch):
    """HyDE 生成が失敗した場合、元のクエリにフォールバックして retrieval が実行されることを確認。"""
    monkeypatch.setattr(server.config, "HYDE_ENABLED", True)

    class FailingHyDEClient(ChatClient):
        async def stream_chat(self, messages, model=None):
            raise RuntimeError("HyDE failed")
            yield ""  # noqa: F501

    def mock_make_chat_client(timeout=None):
        return FailingHyDEClient()

    monkeypatch.setattr(server, "_make_chat_client", mock_make_chat_client)

    retrieved_query = None

    def fake_retrieve(query, top_k, book_id=None):
        nonlocal retrieved_query
        retrieved_query = query
        return _fake_retrieve(query, top_k, book_id)

    monkeypatch.setattr(server, "_retrieve", fake_retrieve)

    _client.post("/api/chat", json={"query": "元の質問"})
    assert retrieved_query == "元の質問"
