"""VectorStore 実装: pgvector(PostgreSQL)。開発(Docker)/本番(Aurora)で同一実装。

ベクトルは pgvector のテキストリテラル "[v1,v2,...]" + ::vector キャストで渡す
（numpy 等の追加依存を避けるため）。距離はコサイン（<=>）。
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.rows import dict_row

from .base import VectorStore

_UPSERT = """
INSERT INTO chunks
    (book_id, chunk_index, title, author, chapter, section, page, text, embedding, embed_model)
VALUES
    (%(book_id)s, %(chunk_index)s, %(title)s, %(author)s, %(chapter)s,
     %(section)s, %(page)s, %(text)s, %(embedding)s::vector, %(embed_model)s)
ON CONFLICT (book_id, chunk_index) DO UPDATE SET
    title = EXCLUDED.title, author = EXCLUDED.author, chapter = EXCLUDED.chapter,
    section = EXCLUDED.section, page = EXCLUDED.page, text = EXCLUDED.text,
    embedding = EXCLUDED.embedding, embed_model = EXCLUDED.embed_model
"""


def _vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


class PgVectorStore(VectorStore):
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=False)

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            for chunk, vec in zip(chunks, vectors, strict=True):
                cur.execute(_UPSERT, {**chunk, "embedding": _vec_literal(vec)})

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        book_id: str | None = None,
        embed_model: str | None = None,
    ) -> list[dict]:
        """ベクトル検索。book_id と embed_model を指定して絞り込むことが可能。"""
        conditions: list[str] = []
        params: dict = {"qv": _vec_literal(query_vector), "k": top_k}

        if book_id is not None:
            conditions.append("book_id = %(book_id)s")
            params["book_id"] = book_id

        if embed_model is not None:
            conditions.append("embed_model = %(embed_model)s")
            params["embed_model"] = embed_model

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT book_id, chunk_index, title, author, chapter, section, page, text,
                   1 - (embedding <=> %(qv)s::vector) AS score
            FROM chunks
            {where_clause}
            ORDER BY embedding <=> %(qv)s::vector
            LIMIT %(k)s
        """
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def get_by_indices(self, book_id: str, chunk_indices: Sequence[int]) -> list[dict]:
        """指定 book_id 内の chunk_index 群を取得する（隣接チャンク展開用）。"""
        if not chunk_indices:
            return []
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                    SELECT book_id, chunk_index, title, author, chapter, section, page, text
                    FROM chunks
                    WHERE book_id = %(book_id)s AND chunk_index = ANY(%(indices)s)
                    ORDER BY chunk_index
                    """,
                {"book_id": book_id, "indices": list(chunk_indices)},
            )
            return cur.fetchall()

    def count_book(self, book_id: str) -> int:
        """その書籍が既に格納されているか（チャンク行数）。増分判定に使う。"""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE book_id = %s", (book_id,))
            return cur.fetchone()[0]

    def delete_book(self, book_id: str) -> int:
        """その書籍のチャンクを全削除（洗い替え/再投入前のクリーンアップ）。"""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE book_id = %s", (book_id,))
            return cur.rowcount

    def search_keyword(self, query: str, top_k: int, book_id: str | None = None) -> list[dict]:
        """pg_bigm 全文検索で query に関連するチャンクを返す（HYBRID_ENABLED 時）。
        book_id でも絞り込み可能。
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        book_cond = "book_id = %(book_id)s AND " if book_id is not None else ""
        sql = f"""
            SELECT book_id, chunk_index, title, author, chapter, section, page, text,
                   bigm_similarity(text, %(q)s) AS score
            FROM chunks
            WHERE {book_cond}text LIKE %(pat)s ESCAPE '\\'
            ORDER BY score DESC
            LIMIT %(k)s
        """
        params: dict = {"q": query, "pat": f"%{escaped}%", "k": top_k}
        if book_id is not None:
            params["book_id"] = book_id
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def atomic_delete_and_upsert(
        self, book_id: str, chunks: list[dict], vectors: list[list[float]]
    ) -> None:
        """Delete existing chunks and insert new ones in a single transaction.

        Guarantees atomicity: either all old chunks are present (rollback),
        or all new chunks are inserted (commit). Never leaves partial state.
        """
        with self.conn.transaction(), self.conn.cursor() as cur:
            # Delete all existing chunks for this book
            cur.execute("DELETE FROM chunks WHERE book_id = %s", (book_id,))
            # Insert all new chunks
            for chunk, vec in zip(chunks, vectors, strict=True):
                cur.execute(_UPSERT, {**chunk, "embedding": _vec_literal(vec)})

    def close(self) -> None:
        self.conn.close()
