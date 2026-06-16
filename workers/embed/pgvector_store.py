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
    (book_id, chunk_index, title, author, chapter, section, page, text, embedding)
VALUES
    (%(book_id)s, %(chunk_index)s, %(title)s, %(author)s, %(chapter)s,
     %(section)s, %(page)s, %(text)s, %(embedding)s::vector)
ON CONFLICT (book_id, chunk_index) DO UPDATE SET
    title = EXCLUDED.title, author = EXCLUDED.author, chapter = EXCLUDED.chapter,
    section = EXCLUDED.section, page = EXCLUDED.page, text = EXCLUDED.text,
    embedding = EXCLUDED.embedding
"""

_SEARCH = """
SELECT book_id, chunk_index, title, author, chapter, section, page, text,
       1 - (embedding <=> %(qv)s::vector) AS score
FROM chunks
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

    def search(self, query_vector: list[float], top_k: int) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_SEARCH, {"qv": _vec_literal(query_vector), "k": top_k})
            return cur.fetchall()

    def close(self) -> None:
        self.conn.close()
