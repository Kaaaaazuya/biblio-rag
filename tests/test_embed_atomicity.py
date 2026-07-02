"""Test atomicity of re-ingestion: DELETE + INSERT must be in same transaction.

Issue #21: When delete_book is followed by embed_and_store, if an exception
occurs between them or during insertion, the database state should be consistent.
Either:
  - All old chunks are present (transaction rolled back)
  - All new chunks are present (transaction committed)
But NOT: old chunks deleted, but only some new chunks inserted.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from workers.embed.pgvector_store import PgVectorStore
from workers.embed.pipeline import embed_and_store_atomic
from workers.embed.base import Embedder, VectorStore


class _FakeEmbedder(Embedder):
    """Test embedder that can optionally fail."""

    def __init__(self, fail_on_call: bool = False):
        self.fail_on_call = fail_on_call
        self.call_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        if self.fail_on_call:
            raise RuntimeError("Embedding failed!")
        return [[0.1 * i for _ in range(1024)] for i in range(len(texts))]

    def search(self, query_vector: list[float], top_k: int) -> list[dict]:
        return []


class _TransactionTrackingStore(VectorStore):
    """Test store that tracks transaction state changes."""

    def __init__(self):
        self.operations: list[dict] = []
        self.in_transaction = False
        self.transaction_committed = False
        self.transaction_rolled_back = False

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        self.operations.append({"op": "upsert", "chunks": chunks})

    def search(self, query_vector: list[float], top_k: int, embed_model: str | None = None) -> list[dict]:
        return []

    def atomic_delete_and_upsert(
        self, book_id: str, chunks: list[dict], vectors: list[list[float]]
    ) -> None:
        self.in_transaction = True
        self.operations.append({"op": "delete", "book_id": book_id})
        self.operations.append({"op": "upsert", "chunks": chunks})
        self.transaction_committed = True
        self.in_transaction = False


def test_atomic_delete_and_upsert_is_called_for_reingestion():
    """When re-ingesting an existing book, use atomic operation."""
    store = _TransactionTrackingStore()
    records = [{"book_id": "book1", "chunk_index": 0, "text": "New chunk"}]
    embedder = _FakeEmbedder()

    embed_and_store_atomic("book1", records, embedder, store, embed_model="bge-m3")

    # Verify atomic method was called (delete and upsert in sequence)
    assert len(store.operations) == 2
    assert store.operations[0]["op"] == "delete"
    assert store.operations[0]["book_id"] == "book1"
    assert store.operations[1]["op"] == "upsert"
    assert store.transaction_committed


def test_atomic_delete_happens_before_upsert():
    """DELETE must happen before INSERT in the atomic operation."""
    store = _TransactionTrackingStore()
    records = [{"book_id": "book1", "chunk_index": 0, "text": "New chunk"}]
    embedder = _FakeEmbedder()

    embed_and_store_atomic("book1", records, embedder, store)

    # Verify order
    assert store.operations[0]["op"] == "delete"
    assert store.operations[1]["op"] == "upsert"


def test_embed_happens_before_atomic_operation():
    """Embedding must happen before the atomic delete+insert operation.

    This ensures we have all vectors ready before any database changes.
    """
    store = _TransactionTrackingStore()
    records = [{"book_id": "book1", "chunk_index": 0, "text": "text"}]
    embedder = _FakeEmbedder()

    embed_and_store_atomic("book1", records, embedder, store)

    # Embedder was called (embedding happened)
    assert embedder.call_count == 1


def test_atomic_operation_returns_chunk_count():
    """embed_and_store_atomic returns the count of chunks inserted."""
    store = _TransactionTrackingStore()
    records = [
        {"book_id": "book1", "chunk_index": 0, "text": "chunk 1"},
        {"book_id": "book1", "chunk_index": 1, "text": "chunk 2"},
    ]
    embedder = _FakeEmbedder()

    count = embed_and_store_atomic("book1", records, embedder, store)

    assert count == 2


def test_atomic_operation_empty_records_returns_zero():
    """embed_and_store_atomic with empty records returns 0 and does nothing."""
    store = _TransactionTrackingStore()
    embedder = _FakeEmbedder()

    count = embed_and_store_atomic("book1", [], embedder, store)

    assert count == 0
    assert len(store.operations) == 0


def test_pgvector_store_is_initialized_without_autocommit():
    """PgVectorStore should disable autocommit to support transactions."""
    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        PgVectorStore("postgresql://localhost/test")

        # Verify psycopg.connect was called with autocommit=False
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs.get("autocommit") is False


def test_atomic_delete_and_upsert_in_transaction():
    """The atomic_delete_and_upsert method should use transaction context."""
    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Set up the mock to track transaction usage
        mock_conn.transaction.return_value.__enter__ = MagicMock()
        mock_conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        mock_connect.return_value = mock_conn

        store = PgVectorStore("postgresql://localhost/test")
        chunks = [{"book_id": "book1", "chunk_index": 0, "text": "chunk"}]
        vectors = [[0.1] * 1024]

        store.atomic_delete_and_upsert("book1", chunks, vectors)

        # Verify transaction context was used
        mock_conn.transaction.assert_called_once()


def test_atomic_reingestion_prevents_partial_state():
    """Scenario: Verify that DELETE + INSERT are atomic.

    If we can observe intermediate state, atomicity is broken.
    With proper transaction handling:
    - Either both DELETE and INSERT succeed
    - Or neither happens (rollback)
    - Never: DELETE succeeds but INSERT partial/fails
    """
    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Simulate transaction context manager
        tx_enter = MagicMock(return_value=None)
        tx_exit = MagicMock(return_value=False)
        mock_conn.transaction.return_value.__enter__ = tx_enter
        mock_conn.transaction.return_value.__exit__ = tx_exit

        cursor_enter = MagicMock(return_value=mock_cursor)
        cursor_exit = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = cursor_enter
        mock_conn.cursor.return_value.__exit__ = cursor_exit

        mock_connect.return_value = mock_conn

        store = PgVectorStore("postgresql://localhost/test")
        chunks = [{"book_id": "book1", "chunk_index": 0, "text": "chunk"}]
        vectors = [[0.1] * 1024]

        store.atomic_delete_and_upsert("book1", chunks, vectors)

        # Verify both operations were in the same transaction context
        assert mock_conn.transaction.called
        # Cursor was used within transaction context
        assert mock_conn.cursor.called
        # DELETE and INSERT were both executed within the same cursor
        assert mock_cursor.execute.call_count == 2
        delete_call = mock_cursor.execute.call_args_list[0]
        insert_call = mock_cursor.execute.call_args_list[1]
        assert "DELETE FROM chunks" in delete_call[0][0]
        assert "INSERT INTO chunks" in insert_call[0][0]
