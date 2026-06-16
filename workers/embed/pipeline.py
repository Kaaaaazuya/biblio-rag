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

CHUNKS_DIR = Path("books/chunks")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def embed_and_store(records: list[dict], embedder: Embedder, store: VectorStore) -> int:
    """チャンク群を埋め込み、格納する。格納件数を返す。"""
    if not records:
        return 0
    vectors = embedder.embed([r["text"] for r in records])
    store.upsert(records, vectors)
    return len(records)


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="③ 埋め込み + 格納: JSONL → pgvector")
    parser.add_argument("paths", nargs="*", help="対象 .jsonl（省略時は books/chunks/*.jsonl）")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.paths] if args.paths else sorted(CHUNKS_DIR.glob("*.jsonl"))
    if not paths:
        print(f"JSONL が見つかりません（{CHUNKS_DIR}/*.jsonl または引数で指定）", file=sys.stderr)
        return 1

    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    store = PgVectorStore(config.database_url())
    try:
        for path in paths:
            n = embed_and_store(load_jsonl(path), embedder, store)
            print(f"{path} -> pgvector ({n} chunks)")
    finally:
        store.close()
    return 0
