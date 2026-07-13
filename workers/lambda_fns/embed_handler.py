"""λ-embed: chunks/{book_id}/{n}.jsonl → pgvector へ upsert。

分割ファイル単位で起動され、各起動は純粋な upsert のみ（DELETE は λ-chunk が実行済み）。
"""

from __future__ import annotations

from workers import config
from workers.embed.pgvector_store import PgVectorStore
from workers.embed.pipeline import active_embed_model, embed_and_store, make_embedder
from workers.storage import ObjectStore

from .events import s3_keys_from_event


def handler(event: dict, context: object = None) -> None:
    store = ObjectStore()
    embedder = make_embedder()
    pg = PgVectorStore(config.database_url())
    try:
        for _bucket, key in s3_keys_from_event(event):
            records = store.load_jsonl(key)
            embed_and_store(records, embedder, pg, embed_model=active_embed_model())
    finally:
        pg.close()
