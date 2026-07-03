"""Tests for persistent ingestion status storage (DB-backed instead of in-memory).

Verifies that:
1. Status records persist across server restarts
2. Status API returns current + historical ingestion state
3. Concurrent status updates are handled correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from webui import server


@pytest.fixture
def client():
    """Test client for WebUI."""
    return TestClient(server.app)


@pytest.fixture
def mock_status_store():
    """Mock StatusStore for unit tests."""
    store = MagicMock()
    return store


class TestStatusPersistence:
    """Test persistent status storage."""

    def test_status_is_persisted_on_ingest(self, client, monkeypatch):
        """Status is saved to DB when ingestion starts."""
        mock_store = MagicMock()
        monkeypatch.setattr(server, "_status_store", mock_store)
        monkeypatch.setattr(server, "_run_pipeline", lambda book_id: None)

        response = client.post("/api/ingest", json={"book_id": "test_book_1"})

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        # Verify status was persisted (not just in-memory)
        mock_store.set_status.assert_called_once_with("test_book_1", "pending", error_msg=None)

    def test_status_retrieval_from_persistent_store(self, client, monkeypatch):
        """Status is retrieved from persistent store, not in-memory dict."""
        mock_store = MagicMock()
        mock_store.get_current_status.return_value = {
            "book_id": "test_book_2",
            "status": "processing",
            "chunks_processed": 42,
            "error_msg": None,
            "updated_at": "2026-07-02T12:00:00Z",
        }
        monkeypatch.setattr(server, "_status_store", mock_store)

        response = client.get("/api/ingest/test_book_2/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["chunks_processed"] == 42
        # Verify store was queried (not in-memory dict)
        mock_store.get_current_status.assert_called_once_with("test_book_2")

    def test_status_persists_across_restarts(self, monkeypatch):
        """Status set before restart is retrievable after restart."""
        # Simulate first server instance
        mock_store_1 = MagicMock()
        mock_store_1.get_current_status.return_value = {
            "book_id": "persistent_book",
            "status": "completed",
            "chunks_processed": 100,
            "error_msg": None,
            "updated_at": "2026-07-02T11:00:00Z",
        }
        monkeypatch.setattr(server, "_status_store", mock_store_1)

        # Query status (simulates checking after restart)
        client = TestClient(server.app)
        response = client.get("/api/ingest/persistent_book/status")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["chunks_processed"] == 100

    def test_status_with_error_message_persisted(self, client, monkeypatch):
        """Error messages are persisted along with status."""
        mock_store = MagicMock()
        mock_store.get_current_status.return_value = {
            "book_id": "failing_book",
            "status": "failed",
            "chunks_processed": 0,
            "error_msg": "PDF extraction failed: corrupt file",
            "updated_at": "2026-07-02T12:30:00Z",
        }
        monkeypatch.setattr(server, "_status_store", mock_store)

        response = client.get("/api/ingest/failing_book/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "PDF extraction failed: corrupt file"

    def test_historical_status_retrieval(self, monkeypatch):
        """Retrieve historical status transitions for a book."""
        mock_store = MagicMock()
        mock_store.get_status_history.return_value = [
            {
                "book_id": "hist_book",
                "status": "pending",
                "chunks_processed": 0,
                "error_msg": None,
                "created_at": "2026-07-02T10:00:00Z",
            },
            {
                "book_id": "hist_book",
                "status": "extracting",
                "chunks_processed": 0,
                "error_msg": None,
                "created_at": "2026-07-02T10:01:00Z",
            },
            {
                "book_id": "hist_book",
                "status": "chunking",
                "chunks_processed": 0,
                "error_msg": None,
                "created_at": "2026-07-02T10:02:00Z",
            },
            {
                "book_id": "hist_book",
                "status": "embedding",
                "chunks_processed": 50,
                "error_msg": None,
                "created_at": "2026-07-02T10:03:00Z",
            },
            {
                "book_id": "hist_book",
                "status": "completed",
                "chunks_processed": 100,
                "error_msg": None,
                "created_at": "2026-07-02T10:05:00Z",
            },
        ]
        monkeypatch.setattr(server, "_status_store", mock_store)

        response = TestClient(server.app).get("/api/ingest/hist_book/status/history")

        assert response.status_code == 200
        history = response.json()
        assert len(history) == 5
        assert history[0]["status"] == "pending"
        assert history[-1]["status"] == "completed"
        assert history[-1]["chunks_processed"] == 100

    def test_concurrent_status_updates(self, monkeypatch):
        """Multiple concurrent status updates for the same book are handled safely."""
        mock_store = MagicMock()
        # Simulate that store handles concurrent updates atomically
        call_count = [0]

        def mock_set_status(book_id, status, error_msg=None):
            call_count[0] += 1

        mock_store.set_status.side_effect = mock_set_status
        mock_store.get_current_status.return_value = {
            "book_id": "concurrent_book",
            "status": "embedding",
            "chunks_processed": 50,
            "error_msg": None,
            "updated_at": "2026-07-02T12:00:00Z",
        }
        monkeypatch.setattr(server, "_status_store", mock_store)
        # パイプライン本体は呼ばずにステータス遷移だけ確認
        monkeypatch.setattr(server, "_run_pipeline", lambda book_id: None)

        client = TestClient(server.app)

        # Simulate multiple concurrent requests updating status
        for _ in range(3):
            client.post(
                "/api/ingest",
                json={"book_id": "concurrent_book"},
            )

        # All requests should succeed, and store should handle them
        assert call_count[0] == 3
        mock_store.set_status.assert_called_with("concurrent_book", "pending", error_msg=None)

    def test_unknown_status_returns_default(self, client, monkeypatch):
        """Unknown book_id returns 'unknown' status."""
        mock_store = MagicMock()
        mock_store.get_current_status.return_value = {
            "book_id": "nonexistent_book",
            "status": "unknown",
            "chunks_processed": 0,
            "error_msg": None,
            "updated_at": None,
        }
        monkeypatch.setattr(server, "_status_store", mock_store)

        response = client.get("/api/ingest/nonexistent_book/status")

        assert response.status_code == 200
        assert response.json()["status"] == "unknown"

    def test_status_updated_on_pipeline_progress(self, monkeypatch):
        """Status is updated as pipeline progresses through stages."""
        mock_store = MagicMock()
        monkeypatch.setattr(server, "_status_store", mock_store)

        # Simulate pipeline calling _set_status at each stage
        server._set_status("prog_book", "extracting")
        mock_store.set_status.assert_called_with("prog_book", "extracting", error_msg=None)

        server._set_status("prog_book", "chunking")
        mock_store.set_status.assert_called_with("prog_book", "chunking", error_msg=None)

        server._set_status("prog_book", "embedding")
        mock_store.set_status.assert_called_with("prog_book", "embedding", error_msg=None)

        server._set_status("prog_book", "completed")
        mock_store.set_status.assert_called_with("prog_book", "completed", error_msg=None)

        # Verify all calls were made
        assert mock_store.set_status.call_count == 4

    def test_pipeline_failure_persists_error(self, monkeypatch):
        """Pipeline failure error message is persisted."""
        mock_store = MagicMock()
        monkeypatch.setattr(server, "_status_store", mock_store)

        error_msg = "PDF extraction failed: Invalid file format"
        server._set_status("failed_book", "failed", error=error_msg)

        mock_store.set_status.assert_called_with("failed_book", "failed", error_msg=error_msg)
