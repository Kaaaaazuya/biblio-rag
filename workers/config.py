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
