"""E2E: 2nd ステージのローカル AWS パイプライン（Terraform + LocalStack）。

S3(raw) PUT → SQS → λ-extract → λ-chunk → λ-embed → pgvector を実際に通す。
事前に `scripts/2nd_local.sh deploy` でリソースを作っておくこと。
LocalStack / pgvector が起動していなければ skip（通常の `uv run pytest` は影響なし）。

マーカー localstack 付き。明示実行: `uv run pytest -m localstack`。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import boto3
import psycopg
import pytest

pytestmark = pytest.mark.localstack

ENDPOINT = "http://localhost:4566"
DSN = "postgresql://biblio:changeme_local_only@localhost:5432/biblio"
BUCKET = "biblio"
FIXTURE = Path(__file__).parent / "fixtures" / "sample_book.pdf"


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _localstack_up() -> bool:
    try:
        _s3().head_bucket(Bucket=BUCKET)
        return True
    except Exception:
        return False


def _db_up() -> bool:
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _localstack_up(), reason="LocalStack(biblio バケット) 未起動")
@pytest.mark.skipif(not _db_up(), reason="pgvector 未起動")
def test_pipeline_through_localstack():
    book_id = f"e2e-{uuid.uuid4().hex[:8]}"
    s3 = _s3()
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        # raw/ に PUT（メタは S3 object metadata に URL エンコードで載せる）
        s3.put_object(
            Bucket=BUCKET,
            Key=f"raw/{book_id}.pdf",
            Body=FIXTURE.read_bytes(),
            Metadata={"title": "Sample%20Book", "author": "Aozora%20Test"},
        )

        rows = 0
        for _ in range(45):  # 最大 ~135 秒
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM chunks WHERE book_id = %s", (book_id,))
                rows = cur.fetchone()[0]
            if rows > 0:
                break
            time.sleep(3)

        assert rows > 0, "パイプラインが pgvector に書き込まなかった"

        # メタ（URL デコード）とベクトル次元を検証
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, author, embed_model, vector_dims(embedding) "
                "FROM chunks WHERE book_id = %s LIMIT 1",
                (book_id,),
            )
            title, author, embed_model, dims = cur.fetchone()
        assert title == "Sample Book"
        assert author == "Aozora Test"
        assert embed_model == "bge-m3"
        assert dims == 1024
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE book_id = %s", (book_id,))
        conn.close()
        for prefix in (f"raw/{book_id}.pdf", f"normalized/{book_id}.md"):
            with __import__("contextlib").suppress(Exception):
                s3.delete_object(Bucket=BUCKET, Key=prefix)
