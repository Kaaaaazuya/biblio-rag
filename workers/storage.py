"""オブジェクトストレージ（S3 / 開発は MinIO）アクセス。

現状の用途は raw PDF の置き場（将来 WebUI からアップロード）。
開発・本番ともに boto3 を使い、接続先だけ S3_ENDPOINT_URL で切り替える
（MinIO: http://localhost:9000 / 本番: 空 = AWS S3）。
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from workers import config

RAW_PREFIX = "raw/"

# S3 object metadata に載せる書誌情報のキー（値は US-ASCII 制約のため URL エンコード）。
_META_KEYS = ("title", "author")


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

    def put_file(
        self, local_path: str | Path, key: str, metadata: dict[str, str] | None = None
    ) -> None:
        kwargs = {}
        if metadata:
            kwargs["ExtraArgs"] = {"Metadata": metadata}
        self.client.upload_file(str(local_path), self.bucket, key, **kwargs)

    def key_exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def put_bytes(self, key: str, data: bytes, metadata: dict[str, str] | None = None) -> None:
        kwargs = {"Bucket": self.bucket, "Key": key, "Body": data}
        if metadata:
            kwargs["Metadata"] = metadata
        self.client.put_object(**kwargs)

    def put_text(self, key: str, text: str) -> None:
        self.put_bytes(key, text.encode("utf-8"))

    def get_text(self, key: str) -> str:
        return self.get_bytes(key).decode("utf-8")

    def put_jsonl(self, key: str, records: list[dict]) -> None:
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        self.put_text(key, body)

    def load_jsonl(self, key: str) -> list[dict]:
        return [json.loads(line) for line in self.get_text(key).splitlines() if line.strip()]

    def get_meta(self, key: str) -> dict[str, str]:
        """raw PDF の S3 object metadata から title/author を URL デコードして返す。"""
        raw = self.client.head_object(Bucket=self.bucket, Key=key).get("Metadata", {})
        return {k: unquote(raw[k]) for k in _META_KEYS if k in raw}
