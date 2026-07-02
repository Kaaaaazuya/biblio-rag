"""E2E: 取り込みパイプライン全鎖（公開フィクスチャ）。

MinIO / pgvector / Ollama(bge-m3) が動いているときだけ実行する統合テスト。
インフラ未起動なら fail ではなく skip する（通常の `uv run pytest` は影響を受けない）。

設計（docs/adr/0010 参照）:
- 入力は著作権フリーの `tests/fixtures/sample_book.pdf`（実書籍は使わない）。
- 専用 book_id で隔離し、teardown で DB 行 / MinIO オブジェクトを後始末 → 実データを汚さない。
- マーカー e2e 付き。既定では除外、明示実行は `uv run pytest -m e2e`。
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

import httpx
import pytest

from workers import config
from workers.chunk.chunk import chunk_markdown
from workers.embed.ollama_embedder import OllamaEmbedder
from workers.embed.pgvector_store import PgVectorStore
from workers.embed.pipeline import active_embed_model, embed_and_store
from workers.extract.extract import extract_pdf_to_markdown
from workers.storage import ObjectStore

pytestmark = pytest.mark.e2e

FIXTURE = Path(__file__).parent / "fixtures" / "sample_book.pdf"


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{config.OLLAMA_HOST}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


def _db_up() -> bool:
    try:
        PgVectorStore(config.database_url()).close()
        return True
    except Exception:
        return False


def _minio_up() -> bool:
    try:
        ObjectStore().list_keys()
        return True
    except Exception:
        return False


@pytest.fixture
def e2e_book():
    """インフラを確認し、隔離した book_id を用意。fixture を MinIO に置き、後始末する。"""
    missing = [
        name
        for name, ok in (("ollama", _ollama_up()), ("pgvector", _db_up()), ("minio", _minio_up()))
        if not ok
    ]
    if missing:
        pytest.skip(f"E2E インフラ未起動: {', '.join(missing)}（docker compose up が必要）")

    book_id = f"e2e_{uuid.uuid4().hex[:8]}"
    key = f"raw/{book_id}.pdf"
    store = PgVectorStore(config.database_url())
    obj = ObjectStore()
    obj.put_file(FIXTURE, key)
    try:
        yield book_id, store, obj, key
    finally:
        store.delete_book(book_id)
        store.close()
        with contextlib.suppress(Exception):
            config.s3_client().delete_object(Bucket=config.S3_BUCKET, Key=key)


def test_pipeline_end_to_end(e2e_book):
    book_id, store, obj, key = e2e_book

    # ① 抽出: MinIO からバイト取得 → 構造つき Markdown
    md = extract_pdf_to_markdown(obj.get_bytes(key))
    assert md.strip(), "抽出結果が空"
    assert md.lstrip().startswith("#"), "見出しが復元されていない"

    # ② チャンク: 必須メタ列が全レコードに揃う
    meta = {"book_id": book_id, "title": "E2E サンプル", "author": "テスト"}
    records = chunk_markdown(md, meta)
    assert records, "チャンクが 0 件"
    required = {"book_id", "chunk_index", "title", "author", "chapter", "section", "page", "text"}
    for r in records:
        assert required <= r.keys()
        assert r["book_id"] == book_id

    # ③ 埋め込み + 格納: 件数が一致
    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    n = embed_and_store(records, embedder, store)
    assert n == len(records)
    assert store.count_book(book_id) == len(records)

    # ④ 検索: fixture 本文に近いクエリ → 先頭ヒットが当該書籍
    qv = embedder.embed(["段組みを含む紙面でブロック単位に座標で整列させ読み順を安定させる"])[0]
    hits = store.search(qv, top_k=5, embed_model=active_embed_model())
    assert hits, "検索結果が空"
    top = hits[0]
    assert top["book_id"] == book_id, f"先頭が別書籍: {top['book_id']}"
    assert 0.0 <= top["score"] <= 1.0
    assert "読み順" in top["text"]
