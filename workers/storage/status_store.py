"""Persistent ingestion status storage using PostgreSQL.

Replaces in-memory dict with database-backed storage that survives server restarts.
Provides interface for tracking status transitions and maintaining historical records.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row


class StatusStore:
    """PostgreSQL-backed ingestion status store.

    Maintains current status and historical records for each book.
    """

    def __init__(self, dsn: str):
        """Initialize connection to PostgreSQL.

        Args:
            dsn: Database connection string (e.g., postgresql://user:pass@host/db)
        """
        self.conn = psycopg.connect(dsn, autocommit=False)

    def __enter__(self) -> StatusStore:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def set_status(
        self,
        book_id: str,
        status: str,
        error_msg: str | None = None,
        chunks_processed: int = 0,
    ) -> None:
        """Record ingestion status for a book.

        Args:
            book_id: Identifier for the book being ingested
            status: Current status ('pending', 'processing', 'completed', 'failed')
            error_msg: Optional error message if status is 'failed'
            chunks_processed: Number of chunks processed so far
        """
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO ingestion_status
                        (book_id, status, error_msg, chunks_processed, created_at, updated_at)
                    VALUES
                        (%(book_id)s, %(status)s, %(error_msg)s, %(chunks_processed)s, now(), now())
                    """,
                {
                    "book_id": book_id,
                    "status": status,
                    "error_msg": error_msg,
                    "chunks_processed": chunks_processed,
                },
            )

    def get_current_status(self, book_id: str) -> dict | None:
        """Get the most recent status for a book.

        Args:
            book_id: Identifier for the book

        Returns:
            Dict with keys: book_id, status, chunks_processed, error_msg, updated_at
            Returns None if no status found for this book
        """
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                    SELECT book_id, status, chunks_processed, error_msg, updated_at
                    FROM ingestion_status
                    WHERE book_id = %(book_id)s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                {"book_id": book_id},
            )
            return cur.fetchone()

    def get_status_history(self, book_id: str) -> list[dict]:
        """Get all status transitions for a book in chronological order.

        Args:
            book_id: Identifier for the book

        Returns:
            List of dicts with keys: book_id, status, chunks_processed, error_msg, created_at
        """
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                    SELECT book_id, status, chunks_processed, error_msg, created_at
                    FROM ingestion_status
                    WHERE book_id = %(book_id)s
                    ORDER BY created_at ASC
                    """,
                {"book_id": book_id},
            )
            return cur.fetchall()

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()
