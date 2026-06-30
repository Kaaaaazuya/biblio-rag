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

_SEARCH = """
SELECT book_id, chunk_index, title, author, chapter, section, page, text,
       1 - (embedding <=> %(qv)s::vector) AS score
FROM chunks
ORDER BY embedding <=> %(qv)s::vector
LIMIT %(k)s
"""

_SEARCH_WITH_BOOK_ID = """
SELECT book_id, chunk_index, title, author, chapter, section, page, text,
       1 - (embedding <=> %(qv)s::vector) AS score
FROM chunks
WHERE book_id = %(book_id)s
ORDER BY embedding <=> %(qv)s::vector
LIMIT %(k)s
"""


def _vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


class PgVectorStore(VectorStore):
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True)

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        with self.conn.cursor() as cur:
            for chunk, vec in zip(chunks, vectors, strict=True):
                cur.execute(_UPSERT, {**chunk, "embedding": _vec_literal(vec)})

    def search(
        self, query_vector: list[float], top_k: int, book_id: str | None = None
    ) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            if book_id is not None:
                cur.execute(
                    _SEARCH_WITH_BOOK_ID,
                    {"qv": _vec_literal(query_vector), "k": top_k, "book_id": book_id},
                )
            else:
                cur.execute(_SEARCH, {"qv": _vec_literal(query_vector), "k": top_k})
            return cur.fetchall()

    def count_book(self, book_id: str) -> int:
        """その書籍が既に格納されているか（チャンク行数）。増分判定に使う。"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE book_id = %s", (book_id,))
            return cur.fetchone()[0]

    def delete_book(self, book_id: str) -> int:
        """その書籍のチャンクを全削除（洗い替え/再投入前のクリーンアップ）。"""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE book_id = %s", (book_id,))
            return cur.rowcount

    def search_keyword(self, query: str, top_k: int, book_id: str | None = None) -> list[dict]:
        """pg_bigm 全文検索で query に関連するチャンクを返す（HYBRID_ENABLED 時）。"""
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
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def close(self) -> None:
        self.conn.close()
