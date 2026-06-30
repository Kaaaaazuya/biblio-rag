"""WebUI 足場: PDF アップロードを「S3 + 静的ファイル」で実現する最小バックエンド。

設計（本番にそのまま乗る形）:
  - ファイル本体はバックエンドを経由せず、ブラウザから **presigned URL で S3(MinIO) に直接 PUT**。
  - バックエンドは「署名の発行」と「メタデータ(title/author)の S3 保存」だけを担う軽量 API。
  - フロントは `webui/static/` の静的ファイル（HTML/JS）。
  - フレームワークは Starlette（軽量・pydantic 非依存）。

開発起動: uv run uvicorn webui.server:app --reload --port 8000
  → http://localhost:8000 を開く（事前に docker compose で MinIO を起動しておく）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import threading
from pathlib import Path
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTasks
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from workers import config
from workers.storage import RAW_PREFIX

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# 取り込みステータスのインメモリストア（プロセス再起動でリセット）
_status: dict[str, dict] = {}
_status_lock = threading.Lock()


def _set_status(book_id: str, status: str, error: str | None = None) -> None:
    with _status_lock:
        _status[book_id] = {"status": status, "error": error}


def _run_pipeline(book_id: str) -> None:
    """extract → chunk → embed を同期実行する。BackgroundTask から呼ばれる。"""
    try:
        from workers.chunk.chunk import HeuristicChunker
        from workers.embed.pgvector_store import PgVectorStore
        from workers.embed.pipeline import active_embed_model, embed_and_store, make_embedder
        from workers.extract.extract import extract_pdf_to_markdown
        from workers.storage import ObjectStore

        _set_status(book_id, "extracting")
        store = ObjectStore()
        pdf_bytes = store.get_bytes(f"{RAW_PREFIX}{book_id}.pdf")
        md = extract_pdf_to_markdown(pdf_bytes)
        store.put_text(f"normalized/{book_id}.md", md)

        _set_status(book_id, "chunking")
        meta = store.get_meta(f"{RAW_PREFIX}{book_id}.pdf")
        meta["book_id"] = book_id
        records = HeuristicChunker().chunk(md, meta)
        store.put_jsonl(f"chunks/{book_id}.jsonl", records)

        _set_status(book_id, "embedding")
        embedder = make_embedder()
        vec_store = PgVectorStore(config.database_url())
        try:
            embed_and_store(records, embedder, vec_store, embed_model=active_embed_model())
        finally:
            vec_store.close()

        _set_status(book_id, "done")
    except Exception as e:  # noqa: BLE001
        _set_status(book_id, "failed", str(e))


def _safe_name(name: str) -> str:
    """パス区切りや危険文字を除いたファイル名（日本語は許可）。不正なら ValueError。"""
    base = Path(name).name  # ディレクトリ要素を除去
    base = re.sub(r"[\x00-\x1f/\\]", "", base).strip()
    if not base or base in {".", ".."}:
        raise ValueError("不正なファイル名です")
    return base


def _retrieve(query: str, top_k: int, book_id: str | None = None) -> list[dict]:
    """クエリを埋め込み、pgvector から類似チャンクを取得する（同期・スレッドで実行）。

    HYDE_ENABLED   : 仮説回答を生成してからベクトル化する
    HYBRID_ENABLED : pg_bigm キーワード検索と RRF 融合する
    RERANK_ENABLED : CrossEncoder で再スコアリングして top_k に絞る
    book_id        : 指定時はその書籍のチャンクのみを対象にする
    """
    from workers.embed.ollama_embedder import OllamaEmbedder
    from workers.embed.pgvector_store import PgVectorStore

    search_query = query
    if config.HYDE_ENABLED:
        search_query = _hyde(query)

    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    vec = embedder.embed([search_query])[0]

    candidate_k = max(config.RERANK_CANDIDATE_K, top_k) if config.RERANK_ENABLED else top_k
    store = PgVectorStore(config.database_url())
    try:
        chunks = store.search(vec, top_k=candidate_k, book_id=book_id)
        if config.HYBRID_ENABLED:
            try:
                chunks = _hybrid_rrf(query, chunks, store, candidate_k, book_id=book_id)
            except Exception as e:
                logger.warning(
                    "HYBRID_ENABLED=true だがキーワード検索失敗（VEC検索にフォールバック）: %s",
                    e,
                    exc_info=True,
                )
    finally:
        store.close()

    if config.RERANK_ENABLED and chunks:
        with contextlib.suppress(Exception):
            from workers.rerank import SentenceReranker

            chunks = SentenceReranker(config.RERANK_MODEL).rerank(query, chunks, top_k)

    return chunks


def _hyde(query: str) -> str:
    """クエリへの仮説回答を CHAT_MODEL で生成して返す（HyDE）。"""
    resp = httpx.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json={
            "model": config.CHAT_MODEL,
            "messages": [
                {"role": "user", "content": f"次の質問に対して簡潔に答えてください: {query}"}
            ],
            "stream": False,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    content = resp.json().get("message", {}).get("content") or ""
    return content if content else query


def _hybrid_rrf(
    query: str,
    vec_chunks: list[dict],
    store,
    top_k: int,
    k: int = 60,
    book_id: str | None = None,
) -> list[dict]:
    """ベクトル検索結果とキーワード検索結果を RRF で融合する（HYBRID_ENABLED 時）。"""
    kw_chunks = store.search_keyword(query, top_k=top_k, book_id=book_id)

    scores: dict[str, float] = {}
    id_to_chunk: dict[str, dict] = {}

    for rank, c in enumerate(vec_chunks):
        cid = f"{c['book_id']}:{c['chunk_index']}"
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        id_to_chunk[cid] = c

    for rank, c in enumerate(kw_chunks):
        cid = f"{c['book_id']}:{c['chunk_index']}"
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        id_to_chunk[cid] = c

    return [
        id_to_chunk[cid] for cid in sorted(scores, key=scores.__getitem__, reverse=True)[:top_k]
    ]


_PERSONA_PREFIXES: dict[str, str] = {
    "senior": (
        "あなたは経験豊富で優しい先輩エンジニアです。"
        "丁寧に、背景や理由も含めて説明してください。\n\n"
    ),
    "strict": (
        "あなたは厳格な先生です。要点を簡潔に伝え、改善の余地があれば率直に指摘してください。\n\n"
    ),
    "simple": (
        "あなたは親切な入門書の著者です。"
        "専門用語は避け、具体例を使って初心者向けに説明してください。\n\n"
    ),
}

_LANG_INSTRUCTIONS: dict[str, str] = {
    "ja": "回答は必ず日本語で行ってください。",
    "en": "You must respond in English.",
}

_SYSTEM_PROMPT = """\
{persona}Answer questions based on the provided reference text.
Use only the reference text below as the basis for your answer.
If the answer is not found in the reference text, say so clearly.
{lang}
{citation_instruction}
Reference text:
{context}"""

_CITATION_INSTRUCTION = (
    "各参考文章には番号が付いています。"
    "回答中で情報を使ったときは [1] [2] の形で引用番号を示してください。\n"
)


async def chat(request: Request) -> StreamingResponse:
    """RAG チャット: クエリ埋め込み → pgvector 検索 → Ollama 生成（SSE ストリーム）。"""
    body = await request.json()
    query = (body.get("query") or "").strip()
    history: list[dict] = body.get("history", [])
    top_k: int = int(body.get("top_k", 5))
    persona: str = body.get("persona", "")
    lang: str = body.get("lang", "ja")
    book_id_raw = body.get("book_id")
    if book_id_raw is not None and not isinstance(book_id_raw, str):
        return JSONResponse({"detail": "book_id は文字列である必要があります"}, status_code=400)
    book_id: str | None = book_id_raw or None

    if not query:
        return JSONResponse({"detail": "query は必須です"}, status_code=400)

    loop = asyncio.get_running_loop()
    chunks = await loop.run_in_executor(None, _retrieve, query, top_k, book_id)

    if config.CITATION_ENABLED:
        context = "\n\n".join(
            f"[{i}] 【{c.get('title', '')}｜{c.get('chapter', '')}】\n{c['text']}"
            for i, c in enumerate(chunks, 1)
        )
    else:
        context = "\n\n".join(
            f"【{c.get('title', '')}｜{c.get('chapter', '')}】\n{c['text']}" for c in chunks
        )
    sources = [
        {k: c.get(k) for k in ("title", "author", "chapter", "section", "page", "text")}
        for c in chunks
    ]
    system_content = _SYSTEM_PROMPT.format(
        persona=_PERSONA_PREFIXES.get(persona, ""),
        lang=_LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["ja"]),
        citation_instruction=_CITATION_INSTRUCTION if config.CITATION_ENABLED else "",
        context=context,
    )
    messages = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": query},
    ]

    async def event_stream():
        src_msg = json.dumps({"type": "sources", "sources": sources}, ensure_ascii=False)
        yield f"data: {src_msg}\n\n"
        try:
            async with (
                httpx.AsyncClient(timeout=120.0) as client,
                client.stream(
                    "POST",
                    f"{config.OLLAMA_HOST}/api/chat",
                    json={"model": config.CHAT_MODEL, "messages": messages, "stream": True},
                ) as resp,
            ):
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if err := data.get("error"):
                        msg = json.dumps(
                            {"type": "error", "message": f"Ollama: {err}"}, ensure_ascii=False
                        )
                        yield f"data: {msg}\n\n"
                        return
                    if content := data.get("message", {}).get("content", ""):
                        msg = json.dumps({"type": "token", "content": content}, ensure_ascii=False)
                        yield f"data: {msg}\n\n"
                    if data.get("done"):
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
        except Exception as e:  # noqa: BLE001
            msg = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {msg}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    """書籍メタデータ（title/author）を S3 object metadata に保存する。

    presigned URL でアップロード済みの raw PDF に copy_object で metadata を付与する。
    S3 object metadata は US-ASCII のみ → 日本語は URL エンコード済みで格納。
    """
    body = await request.json()
    title = (body.get("title") or "").strip()
    author = (body.get("author") or "").strip()
    if not title or not author:
        return JSONResponse({"detail": "title と author は必須です"}, status_code=400)
    try:
        book_id = _safe_name(body.get("book_id", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)

    key = f"{RAW_PREFIX}{book_id}.pdf"
    s3 = config.s3_client()
    try:
        s3.copy_object(
            Bucket=config.S3_BUCKET,
            CopySource={"Bucket": config.S3_BUCKET, "Key": key},
            Key=key,
            Metadata={"title": quote(title), "author": quote(author)},
            MetadataDirective="REPLACE",
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"メタデータ保存失敗: {e}"}, status_code=500)
    return JSONResponse({"book_id": book_id})


async def ingest(request: Request) -> JSONResponse:
    """取り込みパイプライン（extract→chunk→embed）をバックグラウンドで起動する。"""
    body = await request.json()
    try:
        book_id = _safe_name(body.get("book_id", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    _set_status(book_id, "pending")
    tasks = BackgroundTasks()
    tasks.add_task(_run_pipeline, book_id)
    return JSONResponse({"book_id": book_id, "status": "pending"}, background=tasks)


async def ingest_status(request: Request) -> JSONResponse:
    """取り込みステータスを返す。"""
    book_id = request.path_params["book_id"]
    with _status_lock:
        s = dict(_status.get(book_id, {"status": "unknown", "error": None}))
    return JSONResponse(s)


app = Starlette(
    routes=[
        Route("/api/presign", presign, methods=["POST"]),
        Route("/api/meta", save_meta, methods=["POST"]),
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/ingest/{book_id}/status", ingest_status, methods=["GET"]),
        Route("/api/chat", chat, methods=["POST"]),
        # 静的フロント（最後にマウント。html=True で / に index.html を返す）
        Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"),
    ]
)
