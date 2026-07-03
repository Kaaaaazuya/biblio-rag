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
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")

# EMBED_BACKEND=bedrock のとき BedrockEmbedder を使う（デフォルトは ollama）
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "ollama")
BEDROCK_EMBED_MODEL = os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")

# RAG 改善フラグ（デフォルトはすべて無効）
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"
HYBRID_ENABLED = os.getenv("HYBRID_ENABLED", "false").lower() == "true"
HYDE_ENABLED = os.getenv("HYDE_ENABLED", "false").lower() == "true"
CITATION_ENABLED = os.getenv("CITATION_ENABLED", "false").lower() == "true"
RERANK_CANDIDATE_K = int(os.getenv("RERANK_CANDIDATE_K", "20"))
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

# チャット履歴（/api/chat の history）の上限。過大なコンテキストが LLM に渡るのを防ぐ。
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "40"))
MAX_HISTORY_TOTAL_CHARS = int(os.getenv("MAX_HISTORY_TOTAL_CHARS", "20000"))

# スコア閾値未満のチャンクを除外し、「該当情報なし」を明示する（幻覚対策）
SCORE_THRESHOLD_ENABLED = os.getenv("SCORE_THRESHOLD_ENABLED", "false").lower() == "true"
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.5"))

# ヒットしたチャンクの前後（chunk_index ±window）を追加取得し、文脈の連続性を補う
ADJACENT_CHUNK_ENABLED = os.getenv("ADJACENT_CHUNK_ENABLED", "false").lower() == "true"
ADJACENT_CHUNK_WINDOW = int(os.getenv("ADJACENT_CHUNK_WINDOW", "1"))

# オブジェクトストレージ（開発: MinIO / 本番: AWS S3）
# 本番は S3_ENDPOINT_URL を空にすると boto3 が AWS S3 を指す。
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None
S3_BUCKET = os.getenv("S3_BUCKET", "biblio")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")

# WebUI 認証（公開前提での保護）
# WEBUI_AUTH_ENABLED=false（デフォルト）時はローカル開発で認証なし
WEBUI_AUTH_ENABLED = os.getenv("WEBUI_AUTH_ENABLED", "false").lower() == "true"
WEBUI_AUTH_METHOD = os.getenv("WEBUI_AUTH_METHOD", "token")  # token または basic
WEBUI_AUTH_TOKEN = os.getenv("WEBUI_AUTH_TOKEN", "")
WEBUI_AUTH_USERNAME = os.getenv("WEBUI_AUTH_USERNAME", "")
WEBUI_AUTH_PASSWORD = os.getenv("WEBUI_AUTH_PASSWORD", "")


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
