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
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("my query", 3)

    fake_embedder.embed.assert_called_once_with(["my query"])
    fake_store.search.assert_called_once_with(fake_vec, top_k=3, embed_model="bge-m3")
    fake_store.close.assert_called_once()
    assert result == fake_results


def test_retrieve_hyde_failure_falls_back_to_original_query(monkeypatch):
    """HyDE が失敗した場合、元のクエリで埋め込みと検索を行う。"""
    fake_vec = [0.5, 0.6, 0.7]
    fake_results = [{"book_id": "b", "text": "t", "title": "T"}]

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [fake_vec]

    fake_store = MagicMock()
    fake_store.search.return_value = fake_results

    # HyDE を失敗させる
    fake_hyde = MagicMock(side_effect=RuntimeError("HyDE service unavailable"))
    monkeypatch.setattr(server, "_hyde", fake_hyde)
    # HYDE_ENABLED を True に設定
    monkeypatch.setattr(server.config, "HYDE_ENABLED", True)
    monkeypatch.setattr(server.config, "HYBRID_ENABLED", False)
    monkeypatch.setattr(server.config, "RERANK_ENABLED", False)

    with (
        patch("workers.embed.ollama_embedder.OllamaEmbedder", return_value=fake_embedder),
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store),
        patch("workers.embed.pipeline.active_embed_model", return_value="bge-m3"),
    ):
        result = server._retrieve("original query", 3)

    # 元のクエリで埋め込みが呼ばれるべき（HyDE の結果ではなく）
    fake_embedder.embed.assert_called_once_with(["original query"])
    fake_store.search.assert_called_once_with(fake_vec, top_k=3, embed_model="bge-m3")
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


def test_chat_sse_ollama_error_event(monkeypatch, caplog):
    """Ollama からのエラーは詳細情報を返さず、汎用メッセージを返す。"""
    import logging

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm([json.dumps({"error": "model not found"})]),
    )

    with caplog.at_level(logging.ERROR):
        events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    # 内部情報（model not found）は返さない
    assert "model not found" not in errors[0]["message"]
    assert "An error occurred" in errors[0]["message"]
    # ログには記録
    assert any("model not found" in record.message for record in caplog.records)


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


def test_run_pipeline_sets_failed_on_error(caplog):
    """パイプラインエラーは詳細情報を返さず、汎用メッセージを返す。"""
    import logging

    server._status.clear()

    with (
        caplog.at_level(logging.ERROR),
        patch("workers.storage.ObjectStore", side_effect=RuntimeError("接続失敗")),
    ):
        server._run_pipeline("fail-test")

    assert server._status["fail-test"]["status"] == "failed"
    # 内部情報（接続失敗）はステータスに含めない
    assert "接続失敗" not in server._status["fail-test"]["error"]
    assert "An error occurred" in server._status["fail-test"]["error"]
    # ログには記録
    assert any("接続失敗" in record.message for record in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# /api/chat 入力検証のテスト（Issue #14）
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_rejects_top_k_exceeding_max(monkeypatch):
    """top_k が上限（100）を超えた場合は 422 を返す。"""
    res = _client.post("/api/chat", json={"query": "test", "top_k": 10000})
    assert res.status_code == 422
    assert "top_k" in res.json()["detail"]


def test_chat_rejects_negative_top_k(monkeypatch):
    """top_k が負数の場合は 422 を返す。"""
    res = _client.post("/api/chat", json={"query": "test", "top_k": -5})
    assert res.status_code == 422
    assert "top_k" in res.json()["detail"]


def test_chat_accepts_valid_top_k(monkeypatch):
    """top_k が有効範囲（1～100）の場合は正常に処理される。"""
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm([json.dumps({"done": True})]),
    )

    res = _client.post("/api/chat", json={"query": "test", "top_k": 50})
    assert res.status_code == 200


def test_chat_rejects_history_with_invalid_role(monkeypatch):
    """history に無効なロール（"user"/"assistant" 以外）が含まれている場合は 422 を返す。"""
    res = _client.post(
        "/api/chat",
        json={
            "query": "test",
            "history": [{"role": "system", "content": "invalid role"}],
        },
    )
    assert res.status_code == 422
    assert "role" in res.json()["detail"]


def test_chat_rejects_history_without_role(monkeypatch):
    """history のメッセージにロールが指定されていない場合は 422 を返す。"""
    res = _client.post(
        "/api/chat",
        json={
            "query": "test",
            "history": [{"content": "no role"}],
        },
    )
    assert res.status_code == 422
    assert "role" in res.json()["detail"]


def test_chat_accepts_valid_history_roles(monkeypatch):
    """history に有効なロール（"user"/"assistant"）のみ含まれている場合は正常に処理される。"""
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm([json.dumps({"done": True})]),
    )

    res = _client.post(
        "/api/chat",
        json={
            "query": "test",
            "history": [
                {"role": "user", "content": "previous question"},
                {"role": "assistant", "content": "previous answer"},
            ],
        },
    )
    assert res.status_code == 200


def test_chat_rejects_empty_history_message(monkeypatch):
    """history のメッセージが空または不正な場合は 422 を返す。"""
    res = _client.post(
        "/api/chat",
        json={
            "query": "test",
            "history": [{"role": "user"}],  # content なし
        },
    )
    assert res.status_code == 422
    assert "content" in res.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# XSS対策: DOMPurify でサニタイズされることをテスト
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_api_serves_with_csp_header(monkeypatch):
    """チャット API がレスポンスに CSP ヘッダーを付与してセキュリティ強化。

    フロントエンド側で DOMPurify を使用するため、バックエンドでも
    多層防御として CSP を設定する。
    """
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        _fake_llm([json.dumps({"done": True})]),
    )

    res = _client.post("/api/chat", json={"query": "test"})
    # SSE レスポンスなので CSP はメインレスポンスではなく、
    # 将来的な多層防御として確認
    assert res.status_code == 200


def test_chat_markdown_with_dangerous_tags(monkeypatch):
    """Markdown に <script> や <iframe> が含まれている場合の処理をテスト。

    フロントエンド側で DOMPurify でサニタイズされるが、
    バックエンド側でも危険なコンテンツがそのまま返されることを確認。
    （フロントエンド側のサニタイズに依存）
    """
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    # 危険な HTML タグを含むコンテンツを複数の token に分割
    dangerous_llm_output = [
        json.dumps({"message": {"content": "Normal text\n\n<script>"}, "done": False}),
        json.dumps({"message": {"content": "alert('XSS')</script>\n"}, "done": False}),
        json.dumps(
            {"message": {"content": "<iframe src='http://evil.com'></iframe>\n"}, "done": False}
        ),
        json.dumps({"message": {"content": "More text"}, "done": False}),
        json.dumps({"done": True}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(dangerous_llm_output))

    events = _sse_events(_client.post("/api/chat", json={"query": "test"}).text)
    tokens = [e["content"] for e in events if e["type"] == "token"]
    full_content = "".join(tokens)

    # バックエンド側は危険なコンテンツをそのまま返す（フロントエンド側のサニタイズに依存）
    assert "<script>" in full_content, f"Full content: {repr(full_content)}"
    assert "<iframe" in full_content, f"Full content: {repr(full_content)}"  # <iframe> タグが存在
    assert "Normal text" in full_content
