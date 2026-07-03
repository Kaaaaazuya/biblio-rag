"""Issue #23/#24: 書籍単位の絞り込み検索・書籍削除機能のテスト（外部サービスはすべてモック）。

- Issue #23: GET /api/books（チャット UI の書籍選択用の一覧取得）
- Issue #24: DELETE /api/books/{book_id}（オブジェクトストレージ + pgvector を横断した削除）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws
from starlette.testclient import TestClient

from webui import server
from workers.storage import ObjectStore

_client = TestClient(server.app)

BUCKET = "test-biblio"


@pytest.fixture
def object_store():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield ObjectStore(client=client, bucket=BUCKET)


# ─────────────────────────────────────────────────────────────────────────────
# ObjectStore.delete_book_files
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_book_files_removes_raw_normalized_chunks(object_store):
    object_store.put_bytes("raw/book1.pdf", b"pdf")
    object_store.put_text("normalized/book1.md", "# 本文")
    object_store.put_jsonl("chunks/book1.jsonl", [{"book_id": "book1", "chunk_index": 0}])

    object_store.delete_book_files("book1")

    keys = object_store.list_keys()
    assert "raw/book1.pdf" not in keys
    assert "normalized/book1.md" not in keys
    assert "chunks/book1.jsonl" not in keys


def test_delete_book_files_missing_files_no_error(object_store):
    """該当ファイルが一部（または全部）存在しなくてもエラーにならない。"""
    object_store.delete_book_files("nonexistent_book")  # 例外が起きないことを確認


def test_delete_book_files_does_not_affect_other_books(object_store):
    object_store.put_bytes("raw/book1.pdf", b"pdf1")
    object_store.put_bytes("raw/book2.pdf", b"pdf2")

    object_store.delete_book_files("book1")

    keys = object_store.list_keys()
    assert "raw/book1.pdf" not in keys
    assert "raw/book2.pdf" in keys


# ─────────────────────────────────────────────────────────────────────────────
# PgVectorStore.list_books
# ─────────────────────────────────────────────────────────────────────────────


def test_pgvector_list_books_returns_distinct_books():
    from workers.embed.pgvector_store import PgVectorStore

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        {"book_id": "b1", "title": "本1", "author": "著者1"},
        {"book_id": "b2", "title": "本2", "author": "著者2"},
    ]

    with patch("workers.embed.pgvector_store.psycopg.connect", return_value=mock_conn):
        store = PgVectorStore("dsn://fake")
        result = store.list_books()

    sql = mock_cur.execute.call_args[0][0]
    assert "DISTINCT" in sql.upper()
    assert result == [
        {"book_id": "b1", "title": "本1", "author": "著者1"},
        {"book_id": "b2", "title": "本2", "author": "著者2"},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/books
# ─────────────────────────────────────────────────────────────────────────────


def test_list_books_endpoint_returns_books(monkeypatch):
    fake_store = MagicMock()
    fake_store.list_books.return_value = [{"book_id": "b1", "title": "本1", "author": "著者1"}]

    with patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_store):
        res = _client.get("/api/books")

    assert res.status_code == 200
    assert res.json() == [{"book_id": "b1", "title": "本1", "author": "著者1"}]
    fake_store.close.assert_called_once()


def test_list_books_endpoint_handles_db_error():
    with patch("workers.embed.pgvector_store.PgVectorStore", side_effect=RuntimeError("db down")):
        res = _client.get("/api/books")

    assert res.status_code == 500
    assert "detail" in res.json()


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/books/{book_id}
# ─────────────────────────────────────────────────────────────────────────────


def _mock_status_store(monkeypatch, status: str | None = "completed"):
    """delete_book のガード判定用に _status_store をモックする。"""
    fake_status_store = MagicMock()
    fake_status_store.get_current_status.return_value = (
        {"book_id": "book1", "status": status} if status is not None else None
    )
    monkeypatch.setattr(server, "_status_store", fake_status_store)
    return fake_status_store


def test_delete_book_endpoint_deletes_from_vector_store_and_object_store(monkeypatch):
    _mock_status_store(monkeypatch, status="completed")
    fake_vec_store = MagicMock()
    fake_vec_store.delete_book.return_value = 12
    fake_object_store = MagicMock()

    with (
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_vec_store),
        patch("workers.storage.ObjectStore", return_value=fake_object_store),
    ):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 200
    assert res.json() == {"book_id": "book1", "deleted_chunks": 12}
    fake_vec_store.delete_book.assert_called_once_with("book1")
    fake_vec_store.close.assert_called_once()
    fake_object_store.delete_book_files.assert_called_once_with("book1")


def test_delete_book_endpoint_allows_when_no_status_record(monkeypatch):
    """ステータス記録が存在しない（unknown）book_id も削除を許可する。"""
    _mock_status_store(monkeypatch, status=None)
    fake_vec_store = MagicMock()
    fake_vec_store.delete_book.return_value = 0
    fake_object_store = MagicMock()

    with (
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_vec_store),
        patch("workers.storage.ObjectStore", return_value=fake_object_store),
    ):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 200


def test_delete_book_endpoint_rejects_while_ingestion_in_progress(monkeypatch):
    """取り込み中（pending/processing）の書籍は削除を拒否する（Issue #24 code review 指摘）。"""
    _mock_status_store(monkeypatch, status="processing")
    fake_vec_store = MagicMock()

    with patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_vec_store):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 409
    fake_vec_store.delete_book.assert_not_called()


def test_delete_book_endpoint_partial_failure_reports_object_store_error(monkeypatch):
    """pgvector 削除が成功し ObjectStore 削除が失敗した場合、
    500（全体失敗）ではなく部分成功を明示するレスポンスを返す（Issue #24 code review 指摘）。
    """
    _mock_status_store(monkeypatch, status="completed")
    fake_vec_store = MagicMock()
    fake_vec_store.delete_book.return_value = 7
    fake_object_store = MagicMock()
    fake_object_store.delete_book_files.side_effect = RuntimeError("S3 down")

    with (
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_vec_store),
        patch("workers.storage.ObjectStore", return_value=fake_object_store),
    ):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 502
    body = res.json()
    assert body["book_id"] == "book1"
    assert body["deleted_chunks"] == 7
    fake_vec_store.delete_book.assert_called_once_with("book1")


def test_delete_book_endpoint_cleans_up_status_history(monkeypatch):
    """pgvector 削除成功後、ingestion_status の履歴も削除する（Issue #24 code review 指摘）。"""
    fake_status_store = _mock_status_store(monkeypatch, status="completed")
    fake_vec_store = MagicMock()
    fake_vec_store.delete_book.return_value = 3
    fake_object_store = MagicMock()

    with (
        patch("workers.embed.pgvector_store.PgVectorStore", return_value=fake_vec_store),
        patch("workers.storage.ObjectStore", return_value=fake_object_store),
    ):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 200
    fake_status_store.delete_status.assert_called_once_with("book1")


def test_delete_book_endpoint_handles_error(monkeypatch):
    _mock_status_store(monkeypatch, status="completed")
    with patch("workers.embed.pgvector_store.PgVectorStore", side_effect=RuntimeError("db down")):
        res = _client.delete("/api/books/book1")

    assert res.status_code == 500
    assert "detail" in res.json()


def test_delete_book_endpoint_rejects_unsafe_book_id():
    """制御文字のみなど _safe_name が空文字とみなす book_id は拒否する。

    （"/" を含むパスは Starlette のルーティング段階でそもそも {book_id} に
    マッチしないため、ここでは _safe_name のバリデーション自体を検証する）
    """
    res = _client.delete("/api/books/%00")
    assert res.status_code == 400
