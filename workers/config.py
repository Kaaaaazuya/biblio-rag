"""共通設定。リポジトリ root の .env を読み込み、環境変数から値を解決する。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# リポジトリ root の .env を読み込む（無ければ何もしない）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))

# EMBED_BACKEND=bedrock のとき BedrockEmbedder を使う（デフォルトは ollama）
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "ollama")
BEDROCK_EMBED_MODEL = os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")

# オブジェクトストレージ（開発: MinIO / 本番: AWS S3）
# 本番は S3_ENDPOINT_URL を空にすると boto3 が AWS S3 を指す。
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None
S3_BUCKET = os.getenv("S3_BUCKET", "biblio")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")


def s3_client():
    """boto3 S3 クライアント。資格情報は環境変数（.env）から自動解決。"""
    import boto3

    return boto3.client("s3", endpoint_url=S3_ENDPOINT_URL, region_name=AWS_REGION)


def database_url() -> str:
    """DATABASE_URL があればそれを、無ければ POSTGRES_* から組み立てる。"""
    if url := os.getenv("DATABASE_URL"):
        return url
    # 既定値は docker-compose / .env.example と一致させる（.env が無い場合の罠を避ける）
    user = os.getenv("POSTGRES_USER", "biblio")
    password = os.getenv("POSTGRES_PASSWORD", "changeme_local_only")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "biblio")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
