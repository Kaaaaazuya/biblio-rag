"""WebUI 足場: PDF アップロードを「S3 + 静的ファイル」で実現する最小バックエンド。

設計（本番にそのまま乗る形）:
  - ファイル本体はバックエンドを経由せず、ブラウザから **presigned URL で S3(MinIO) に直接 PUT**。
  - バックエンドは「署名の発行」と「メタデータ(title/author)の保存」だけを担う軽量 API。
  - フロントは `webui/static/` の静的ファイル（HTML/JS）。
  - フレームワークは Starlette（軽量・pydantic 非依存）。

開発起動: uv run uvicorn webui.server:app --reload --port 8000
  → http://localhost:8000 を開く（事前に docker compose で MinIO を起動しておく）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from workers import config
from workers.storage import RAW_PREFIX

STATIC_DIR = Path(__file__).parent / "static"
BOOKS_DIR = Path("books")


def _safe_name(name: str) -> str:
    """パス区切りや危険文字を除いたファイル名（日本語は許可）。不正なら ValueError。"""
    base = Path(name).name  # ディレクトリ要素を除去
    base = re.sub(r"[\x00-\x1f/\\]", "", base).strip()
    if not base or base in {".", ".."}:
        raise ValueError("不正なファイル名です")
    return base


async def presign(request: Request) -> JSONResponse:
    """raw/<filename> への PUT 用 presigned URL を発行する。"""
    body = await request.json()
    try:
        name = _safe_name(body.get("filename", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    if not name.lower().endswith(".pdf"):
        return JSONResponse({"detail": "PDF ファイルのみ対応しています"}, status_code=400)

    key = f"{RAW_PREFIX}{name}"
    url = config.s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": config.S3_BUCKET,
            "Key": key,
            "ContentType": body.get("content_type", "application/pdf"),
        },
        ExpiresIn=3600,
    )
    return JSONResponse({"url": url, "key": key, "book_id": Path(name).stem})


async def save_meta(request: Request) -> JSONResponse:
    """書籍メタデータ（title/author）をサイドカー JSON に保存する。"""
    body = await request.json()
    title = (body.get("title") or "").strip()
    author = (body.get("author") or "").strip()
    if not title or not author:
        return JSONResponse({"detail": "title と author は必須です"}, status_code=400)
    try:
        book_id = _safe_name(body.get("book_id", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)

    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    path = BOOKS_DIR / f"{book_id}.meta.json"
    path.write_text(
        json.dumps({"title": title, "author": author}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return JSONResponse({"book_id": book_id, "meta": str(path)})


app = Starlette(
    routes=[
        Route("/api/presign", presign, methods=["POST"]),
        Route("/api/meta", save_meta, methods=["POST"]),
        # 静的フロント（最後にマウント。html=True で / に index.html を返す）
        Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"),
    ]
)
