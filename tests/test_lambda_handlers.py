"""2nd ステージ Lambda ハンドラのユニットテスト（moto で S3 / DB はフェイク）。

各ハンドラは S3 イベント（SQS 経由）を受け、既存の workers/ ロジックを薄くラップする。
- extract: raw/{id}.pdf → normalized/{id}.md
- chunk  : normalized/{id}.md (+ raw のメタ) → chunks/{id}/{n}.jsonl（分割）
- embed  : chunks/{id}/{n}.jsonl → pgvector へ upsert
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import boto3
import pytest
from moto import mock_aws

from workers.lambda_fns import chunk_handler, embed_handler, extract_handler
from workers.lambda_fns.events import s3_keys_from_event

BUCKET = "test-biblio"
FIXTURE = Path(__file__).parent / "fixtures" / "sample_book.pdf"


def _sqs_s3_event(bucket: str, key: str) -> dict:
    """S3 通知 → SQS の二段ラップを模した Lambda イベント。"""
    s3_event = {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}
    return {"Records": [{"body": json.dumps(s3_event)}]}


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


# --- event parsing ---


def test_s3_keys_from_sqs_wrapped_event():
    event = _sqs_s3_event(BUCKET, "raw/book.pdf")
    assert s3_keys_from_event(event) == [(BUCKET, "raw/book.pdf")]


def test_s3_keys_url_decodes_key():
    event = _sqs_s3_event(BUCKET, "raw/my+book.pdf")
    assert s3_keys_from_event(event) == [(BUCKET, "raw/my book.pdf")]


# --- extract handler ---


def test_extract_handler_writes_normalized_md(s3, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    from workers.storage import ObjectStore

    store = ObjectStore(client=s3, bucket=BUCKET)
    store.put_bytes("raw/book.pdf", FIXTURE.read_bytes())
    monkeypatch.setattr(extract_handler, "ObjectStore", lambda: store)

    extract_handler.handler(_sqs_s3_event(BUCKET, "raw/book.pdf"))

    md = store.get_text("normalized/book.md")
    assert md.strip()  # 何らかの Markdown が出力された


# --- chunk handler ---


def test_chunk_handler_splits_and_deletes(s3, monkeypatch):
    from workers.storage import ObjectStore

    store = ObjectStore(client=s3, bucket=BUCKET)
    store.put_text("normalized/book.md", "# 章\n" + "あ。" * 2000 + "\n")
    store.put_bytes("raw/book.pdf", b"x", metadata={"title": quote("題"), "author": quote("著")})
    monkeypatch.setattr(chunk_handler, "ObjectStore", lambda: store)

    deleted = []

    class FakePg:
        def __init__(self, dsn):
            pass

        def delete_book(self, book_id):
            deleted.append(book_id)
            return 0

        def close(self):
            pass

    monkeypatch.setattr(chunk_handler, "PgVectorStore", FakePg)
    monkeypatch.setattr(chunk_handler, "SPLIT_SIZE", 2)

    chunk_handler.handler(_sqs_s3_event(BUCKET, "normalized/book.md"))

    assert deleted == ["book"]  # fan-out 前に一度だけ削除
    keys = store.list_keys("chunks/book/")
    assert len(keys) >= 2  # 分割されている
    recs = store.load_jsonl(keys[0])
    assert recs[0]["title"] == "題" and recs[0]["book_id"] == "book"


# --- embed handler ---


def test_embed_handler_upserts_records(s3, monkeypatch):
    from workers.storage import ObjectStore

    store = ObjectStore(client=s3, bucket=BUCKET)
    recs = [{"book_id": "book", "chunk_index": i, "text": f"t{i}"} for i in range(3)]
    store.put_jsonl("chunks/book/0000.jsonl", recs)
    monkeypatch.setattr(embed_handler, "ObjectStore", lambda: store)

    upserted = {}

    class FakeStore:
        def __init__(self, dsn):
            pass

        def upsert(self, chunks, vectors):
            upserted["chunks"] = chunks
            upserted["vectors"] = vectors

        def close(self):
            pass

    class FakeEmbedder:
        def embed(self, texts):
            return [[0.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed_handler, "PgVectorStore", FakeStore)
    monkeypatch.setattr(embed_handler, "make_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(embed_handler, "active_embed_model", lambda: "bge-m3")

    embed_handler.handler(_sqs_s3_event(BUCKET, "chunks/book/0000.jsonl"))

    assert len(upserted["chunks"]) == 3
    assert upserted["chunks"][0]["embed_model"] == "bge-m3"
