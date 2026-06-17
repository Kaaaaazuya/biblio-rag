"""λ-chunk: normalized/{book_id}.md → chunks/{book_id}/{n}.jsonl（分割）。

メタ（title/author）は raw PDF の S3 object metadata から取得する（単一の正本）。
fan-out 前に一度だけ旧チャンクを削除し、後段の並列 embed は純粋 upsert に保つ（ADR 0011）。
"""

from __future__ import annotations

from itertools import batched
from pathlib import PurePosixPath

from workers import config
from workers.chunk.chunk import chunk_markdown
from workers.embed.pgvector_store import PgVectorStore
from workers.storage import ObjectStore

from .events import s3_keys_from_event

# 1 つの embed 起動あたりのチャンク数（Bedrock 逐次呼び出しのタイムアウト回避）。
SPLIT_SIZE = 200


def handler(event: dict, context=None) -> None:
    store = ObjectStore()
    for _bucket, key in s3_keys_from_event(event):
        book_id = PurePosixPath(key).stem
        md = store.get_text(key)
        meta = store.get_meta(f"raw/{book_id}.pdf")
        meta["book_id"] = book_id
        records = chunk_markdown(md, meta)

        # fan-out 前に一度だけ旧チャンクを削除（並列 embed の競合回避）
        pg = PgVectorStore(config.database_url())
        try:
            pg.delete_book(book_id)
        finally:
            pg.close()

        for i, batch in enumerate(batched(records, SPLIT_SIZE, strict=False)):
            store.put_jsonl(f"chunks/{book_id}/{i:04d}.jsonl", list(batch))
