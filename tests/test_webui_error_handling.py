"""WebUI エラーハンドリング: 内部情報露出対策のテスト。

Issue #15: クライアント向けは汎用メッセージ、詳細情報はログのみ。
- save_meta() で S3 エラーが発生した場合、内部情報を返さない
- chat() で例外が発生した場合、内部情報を返さない
- _run_pipeline() で例外が発生した場合、詳細情報をログに記録する
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from webui import server

_client = TestClient(server.app)


# ─────────────────────────────────────────────────────────────────────────────
# save_meta() エラーハンドリング
# ─────────────────────────────────────────────────────────────────────────────


def test_save_meta_s3_error_returns_generic_message(caplog):
    """S3 エラーが発生した場合、内部情報を含まない汎用メッセージを返す。"""
    mock_s3 = MagicMock()
    mock_s3.copy_object.side_effect = ConnectionError("connection to localhost:9000 refused")

    with caplog.at_level(logging.ERROR):
        with patch("workers.config.s3_client", return_value=mock_s3):
            res = _client.post(
                "/api/meta",
                json={"book_id": "test", "title": "テスト書", "author": "著者"},
            )

    assert res.status_code == 503
    data = res.json()
    # 内部情報（localhost:9000）は返さない
    assert "localhost" not in data["detail"]
    assert "refused" not in data["detail"]
    # 汎用メッセージのみ
    assert "Failed to save metadata" in data["detail"]
    # ログには詳細情報を記録
    assert any("localhost:9000" in record.message for record in caplog.records)


def test_save_meta_invalid_filename_returns_400():
    """入力エラーなら 400 を返す（503 ではなく）。"""
    res = _client.post("/api/meta", json={"book_id": "", "title": "テスト", "author": "著者"})
    assert res.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# chat() エンドポイントのエラーハンドリング
# ─────────────────────────────────────────────────────────────────────────────


def _sse_events(text: str) -> list[dict]:
    """SSE レスポンスのテキストから data: 行を抽出する。"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_chat_connection_error_returns_generic_message(caplog, monkeypatch):
    """Ollama 接続エラーが発生した場合、内部情報を含まないメッセージを返す。"""

    def _fake_retrieve(query: str, top_k: int) -> list[dict]:
        return [
            {
                "book_id": "b",
                "text": "テキスト",
                "title": "タイトル",
                "author": "著者",
                "chapter": "1",
                "section": None,
                "page": 1,
            }
        ]

    # AsyncClient の非同期 context manager をモック
    class _FakeAsyncClient:
        async def __aenter__(self):
            raise ConnectionError("connection to localhost:11434 refused")

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return self

    mock_client_class = MagicMock(return_value=_FakeAsyncClient())

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr("httpx.AsyncClient", mock_client_class)

    with caplog.at_level(logging.ERROR):
        res = _client.post("/api/chat", json={"query": "質問"})

    events = _sse_events(res.text)
    errors = [e for e in events if e["type"] == "error"]

    assert len(errors) >= 1
    error_msg = errors[0]["message"]
    # 内部情報（localhost:11434）は返さない
    assert "localhost" not in error_msg
    assert "refused" not in error_msg
    # 汎用メッセージのみ
    assert "An error occurred" in error_msg
    # ログには詳細情報を記録
    assert any("localhost:11434" in record.message for record in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# _run_pipeline() エラーハンドリング
# ─────────────────────────────────────────────────────────────────────────────


def test_run_pipeline_error_logs_details_not_exposed_in_status(caplog):
    """パイプラインエラーが発生した場合、詳細情報はログのみ、ステータスには汎用メッセージ。"""
    server._status.clear()

    error_detail = "connection to localhost:5432 refused"
    with caplog.at_level(logging.ERROR):
        with patch("workers.storage.ObjectStore", side_effect=ConnectionError(error_detail)):
            server._run_pipeline("error-test")

    status = server._status["error-test"]
    assert status["status"] == "failed"
    # 内部情報（localhost:5432）はステータスに含めない
    assert "localhost" not in status["error"]
    assert "5432" not in status["error"]
    # 汎用メッセージのみ
    assert "An error occurred while processing" in status["error"]
    # ログには詳細情報を記録
    assert any(error_detail in record.message for record in caplog.records)
