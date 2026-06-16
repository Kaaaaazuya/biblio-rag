"""オブジェクトストレージ（S3 / 開発は MinIO）アクセス。

現状の用途は raw PDF の置き場（将来 WebUI からアップロード）。
開発・本番ともに boto3 を使い、接続先だけ S3_ENDPOINT_URL で切り替える
（MinIO: http://localhost:9000 / 本番: 空 = AWS S3）。
"""

from __future__ import annotations

from pathlib import Path

from workers import config

RAW_PREFIX = "raw/"


class ObjectStore:
    def __init__(self, client=None, bucket: str | None = None):
        self.client = client or config.s3_client()
        self.bucket = bucket or config.S3_BUCKET

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    def list_pdfs(self, prefix: str = RAW_PREFIX) -> list[str]:
        return [k for k in self.list_keys(prefix) if k.lower().endswith(".pdf")]

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put_file(self, local_path: str | Path, key: str) -> None:
        self.client.upload_file(str(local_path), self.bucket, key)
