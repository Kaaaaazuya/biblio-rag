"""ObjectStore の S3 受け渡しヘルパのユニットテスト（moto で S3 を擬似）。

2nd ステージの Lambda ハンドラが中間ファイル（normalized MD / chunks JSONL）と
メタデータ（title/author を S3 object metadata 経由）を S3 でやり取りするための層。
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from workers.storage import ObjectStore

BUCKET = "test-biblio"


@pytest.fixture
def store():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield ObjectStore(client=client, bucket=BUCKET)


def test_put_get_text_roundtrip(store):
    store.put_text("normalized/book.md", "# 見出し\n本文です\n")
    assert store.get_text("normalized/book.md") == "# 見出し\n本文です\n"


def test_put_load_jsonl_roundtrip(store):
    records = [
        {"book_id": "b", "chunk_index": 0, "text": "あ"},
        {"book_id": "b", "chunk_index": 1, "text": "い"},
    ]
    store.put_jsonl("chunks/book.jsonl", records)
    assert store.load_jsonl("chunks/book.jsonl") == records


def test_get_meta_url_decodes_japanese(store):
    # presign 経由のブラウザ PUT を模擬: title/author は URL エンコードして metadata へ
    from urllib.parse import quote

    store.put_bytes(
        "raw/book.pdf",
        b"pdf-bytes",
        metadata={"title": quote("リーダブルコード"), "author": quote("著者 太郎")},
    )
    meta = store.get_meta("raw/book.pdf")
    assert meta == {"title": "リーダブルコード", "author": "著者 太郎"}


def test_get_meta_missing_keys_returns_empty(store):
    store.put_bytes("raw/nometa.pdf", b"pdf-bytes")
    assert store.get_meta("raw/nometa.pdf") == {}
