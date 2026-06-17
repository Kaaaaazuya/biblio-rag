"""③ パイプライン: chunks/*.jsonl を読み、埋め込み、pgvector に格納する。

CLI: uv run python -m workers.embed            # books/chunks/*.jsonl をすべて格納
     uv run python -m workers.embed a.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from workers import config

from .base import Embedder, VectorStore
from .ollama_embedder import OllamaEmbedder
from .pgvector_store import PgVectorStore


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


CHUNKS_DIR = Path("books/chunks")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def embed_and_store(
    records: list[dict], embedder: Embedder, store: VectorStore, embed_model: str = ""
) -> int:
    """チャンク群を埋め込み、格納する。格納件数を返す。"""
    if not records:
        return 0
    vectors = embedder.embed([r["text"] for r in records])
    if embed_model:
        records = [{**r, "embed_model": embed_model} for r in records]
    store.upsert(records, vectors)
    return len(records)


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="③ 埋め込み + 格納: JSONL → pgvector")
    parser.add_argument("paths", nargs="*", help="対象 .jsonl（省略時は books/chunks/*.jsonl）")
    parser.add_argument("--force", action="store_true", help="格納済みも再埋め込み（洗い替え）")
    args = parser.parse_args(argv)

    is_batch = not args.paths
    paths = [Path(p) for p in args.paths] if args.paths else sorted(CHUNKS_DIR.glob("*.jsonl"))
    if not paths:
        print(f"JSONL が見つかりません（{CHUNKS_DIR}/*.jsonl または引数で指定）", file=sys.stderr)
        return 1

    embedder = make_embedder()
    store = PgVectorStore(config.database_url())
    model_name = active_embed_model()
    try:
        for path in paths:
            records = load_jsonl(path)
            if not records:
                continue
            book_id = records[0]["book_id"]
            exists = store.count_book(book_id) > 0
            # 既定: 一括実行で格納済みはスキップ（--force で洗い替え）
            if is_batch and exists and not args.force:
                print(f"スキップ（格納済み）: {path.name} (book_id={book_id})")
                continue
            if exists:
                store.delete_book(book_id)  # 再投入前に既存を消してクリーンに入れ直す
            n = embed_and_store(records, embedder, store, embed_model=model_name)
            print(f"{path} -> pgvector ({n} chunks)")
    finally:
        store.close()
    return 0
