"""③ パイプライン: chunks/*.jsonl を読み、埋め込み、pgvector に格納する。

CLI: uv run python -m workers.embed            # S3 chunks/ の .jsonl をすべて格納
     uv run python -m workers.embed mybook     # book_id 指定
"""

from __future__ import annotations

import argparse
import sys

from workers import config

from .base import Embedder, VectorStore
from .ollama_embedder import OllamaEmbedder
from .pgvector_store import PgVectorStore

CHUNKS_PREFIX = "chunks/"


def make_embedder() -> Embedder:
    """EMBED_BACKEND 環境変数に応じた Embedder を返す。"""
    if config.EMBED_BACKEND == "bedrock":
        from .bedrock_embedder import BedrockEmbedder

        return BedrockEmbedder(config.BEDROCK_EMBED_MODEL, config.EMBED_DIM, config.AWS_REGION)
    return OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)


def active_embed_model() -> str:
    """現在の EMBED_BACKEND に対応するモデル名を返す（embed_model カラムに格納する値）。"""
    if config.EMBED_BACKEND == "bedrock":
        return config.BEDROCK_EMBED_MODEL
    return config.EMBED_MODEL


def embed_and_store(
    records: list[dict], embedder: Embedder, store: VectorStore, embed_model: str = ""
) -> int:
    """チャンク群を埋め込み、格納する。格納件数を返す。"""
    if not records:
        return 0
    vectors = embedder.embed([r["text"] for r in records])
    # Always set embed_model to ensure it's present in the record dictionary
    # Use provided value or default to currently active model
    model_to_use = embed_model or active_embed_model()
    records = [{**r, "embed_model": model_to_use} for r in records]
    store.upsert(records, vectors)
    return len(records)


def embed_and_store_atomic(
    book_id: str,
    records: list[dict],
    embedder: Embedder,
    store: VectorStore,
    embed_model: str = "",
) -> int:
    """チャンク群を埋め込み、既存チャンクと原子的に置き換える。

    既存チャンク削除と新規チャンク投入を同一トランザクション内で実行し、
    中間状態（削除済みだが一部未投入）の発生を防ぐ。
    """
    if not records:
        return 0
    vectors = embedder.embed([r["text"] for r in records])
    # Always set embed_model to ensure it's present in the record dictionary
    # Use provided value or default to currently active model
    model_to_use = embed_model or active_embed_model()
    records = [{**r, "embed_model": model_to_use} for r in records]
    store.atomic_delete_and_upsert(book_id, records, vectors)
    return len(records)


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="③ 埋め込み + 格納: JSONL → pgvector")
    parser.add_argument("book_ids", nargs="*", help="book_id（省略時は S3 chunks/ を一括処理）")
    parser.add_argument("--force", action="store_true", help="格納済みも再埋め込み（洗い替え）")
    args = parser.parse_args(argv)

    from workers.storage import ObjectStore

    obj_store = ObjectStore()

    if args.book_ids:
        chunk_keys = [f"{CHUNKS_PREFIX}{bid}.jsonl" for bid in args.book_ids]
    else:
        chunk_keys = [k for k in obj_store.list_keys(CHUNKS_PREFIX) if k.endswith(".jsonl")]
    if not chunk_keys:
        print(f"S3 に JSONL がありません（{obj_store.bucket}/{CHUNKS_PREFIX}）", file=sys.stderr)
        return 1

    is_batch = not args.book_ids
    embedder = make_embedder()
    vec_store = PgVectorStore(config.database_url())
    model_name = active_embed_model()
    try:
        for key in chunk_keys:
            records = obj_store.load_jsonl(key)
            if not records:
                continue
            book_id = records[0]["book_id"]
            exists = vec_store.count_book(book_id) > 0
            if is_batch and exists and not args.force:
                print(f"スキップ（格納済み）: {key} (book_id={book_id})")
                continue
            if exists:
                # Re-ingestion: use atomic delete + insert to prevent partial failures
                n = embed_and_store_atomic(
                    book_id, records, embedder, vec_store, embed_model=model_name
                )
            else:
                # New ingestion: regular upsert is fine
                n = embed_and_store(records, embedder, vec_store, embed_model=model_name)
            print(f"s3://{obj_store.bucket}/{key} -> pgvector ({n} chunks)")
    finally:
        vec_store.close()
    return 0
