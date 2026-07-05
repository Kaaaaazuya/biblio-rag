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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from botocore.exceptions import ClientError
from starlette.applications import Starlette
from starlette.background import BackgroundTasks
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from webui.auth import AuthMiddleware
from webui.logging_config import configure_logging
from workers import config
from workers.storage import RAW_PREFIX, StatusStore

if TYPE_CHECKING:
    from workers.chat.base import ChatClient
    from workers.embed.pgvector_store import PgVectorStore

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# アップロードサイズ上限: 500MB
MAX_UPLOAD_SIZE = 500 * 1024 * 1024

# 取り込みステータスの永続ストア（PostgreSQL-backed）
# テスト用のモック インスタンス（None の場合は新規作成）
_status_store: StatusStore | None = None


def _get_status_store() -> StatusStore:
    """Get status store instance.

    Returns the mocked instance if set in tests, otherwise creates a new
    instance per call to ensure thread-safety with psycopg connections.
    Tests can set _status_store to a mock for testing without DB.
    """
    global _status_store
    if _status_store is not None:
        return _status_store
    return StatusStore(config.database_url())


def _set_status(
    book_id: str, status: str, error: str | None = None, chunks_processed: int = 0
) -> None:
    """Record ingestion status to persistent storage."""
    try:
        store = _get_status_store()
        try:
            store.set_status(book_id, status, error_msg=error, chunks_processed=chunks_processed)
        finally:
            if store is not _status_store:
                store.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to persist status for book_id={book_id}: {e}", exc_info=True)


def _run_pipeline(book_id: str) -> None:
    """extract → chunk → embed を同期実行する。BackgroundTask から呼ばれる。"""
    try:
        from workers.chunk.chunk import HeuristicChunker
        from workers.embed.pgvector_store import PgVectorStore
        from workers.embed.pipeline import active_embed_model, embed_and_store, make_embedder
        from workers.extract.extract import extract_pdf_to_markdown
        from workers.storage import ObjectStore

        _set_status(book_id, "processing")
        store = ObjectStore()
        pdf_bytes = store.get_bytes(f"{RAW_PREFIX}{book_id}.pdf")
        md = extract_pdf_to_markdown(pdf_bytes)
        store.put_text(f"normalized/{book_id}.md", md)

        meta = store.get_meta(f"{RAW_PREFIX}{book_id}.pdf")
        meta["book_id"] = book_id
        records = HeuristicChunker().chunk(md, meta)
        store.put_jsonl(f"chunks/{book_id}.jsonl", records)

        embedder = make_embedder()
        vec_store = PgVectorStore(config.database_url())
        try:
            embed_and_store(records, embedder, vec_store, embed_model=active_embed_model())
        finally:
            vec_store.close()

        _set_status(book_id, "completed", chunks_processed=len(records))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Pipeline failed for book_id={book_id}: {e}", exc_info=True)
        _set_status(book_id, "failed", "An error occurred while processing. Please try again.")


def _safe_name(name: str) -> str:
    """パス区切りや危険文字を除いたファイル名（日本語は許可）。不正なら ValueError。"""
    base = Path(name).name  # ディレクトリ要素を除去
    base = re.sub(r"[\x00-\x1f/\\]", "", base).strip()
    if not base or base in {".", ".."}:
        raise ValueError("不正なファイル名です")
    return base


def _object_exists(bucket: str, key: str) -> bool:
    """S3 オブジェクトが存在するかチェック。存在すれば True、しなければ False。"""
    try:
        s3 = config.s3_client()
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        # 404 Not Found (NoSuchKey) なら存在しない
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        # その他のエラーは再スロー（権限不足など）
        raise


def _retrieve(query: str, top_k: int, book_id: str | None = None) -> list[dict]:
    """クエリを埋め込み、pgvector から類似チャンクを取得する（同期・スレッドで実行）。

    HYBRID_ENABLED         : pg_bigm キーワード検索と RRF 融合する
    RERANK_ENABLED         : CrossEncoder で再スコアリングして top_k に絞る
    SCORE_THRESHOLD_ENABLED: ベクトル検索直後にスコア閾値未満の候補を除外する
                             （HYBRID/RERANK 適用前 = キーワードのみのヒットや
                             Rerank の再評価がベクトル類似度の閾値で握りつぶされないようにする）
    ADJACENT_CHUNK_ENABLED : ヒットチャンクの前後（chunk_index ±window）を追加取得する
    book_id                : 指定時はその書籍のチャンクのみを対象にする
    """
    from workers.embed.ollama_embedder import OllamaEmbedder
    from workers.embed.pgvector_store import PgVectorStore
    from workers.embed.pipeline import active_embed_model

    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    vec = embedder.embed([query])[0]

    candidate_k = max(config.RERANK_CANDIDATE_K, top_k) if config.RERANK_ENABLED else top_k
    store = PgVectorStore(config.database_url())
    try:
        chunks = store.search(
            vec, top_k=candidate_k, book_id=book_id, embed_model=active_embed_model()
        )
        if config.SCORE_THRESHOLD_ENABLED:
            # HYBRID/RERANK より前に適用: kw_chunks の bigm スコアや rerank 前のベクトル
            # スコアが後続処理で上書き・無視されるため、ここでのみベクトル類似度と比較する
            chunks = [c for c in chunks if (c.get("score") or 0) >= config.SCORE_THRESHOLD]

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

    if config.ADJACENT_CHUNK_ENABLED and chunks:
        adj_store = PgVectorStore(config.database_url())
        try:
            chunks = _expand_adjacent_chunks(chunks, adj_store, config.ADJACENT_CHUNK_WINDOW)
        finally:
            adj_store.close()

    return chunks


def _expand_adjacent_chunks(chunks: list[dict], store: PgVectorStore, window: int) -> list[dict]:
    """ヒットしたチャンクの前後（chunk_index ±window）を追加取得してマージする。

    ADJACENT_CHUNK_ENABLED 時に呼ばれる。既にヒット済みの chunk_index は
    再取得しない。書籍ごとにグルーピングし、
    最終結果は book_id, chunk_index 順に並べ直す（文脈の連続性のため）。
    """
    seen = {(c["book_id"], c["chunk_index"]) for c in chunks}
    needed: dict[str, set[int]] = {}
    for c in chunks:
        book_id, idx = c["book_id"], c["chunk_index"]
        for offset in range(1, window + 1):
            for neighbor_idx in (idx - offset, idx + offset):
                if neighbor_idx >= 0 and (book_id, neighbor_idx) not in seen:
                    needed.setdefault(book_id, set()).add(neighbor_idx)

    extra: list[dict] = []
    for book_id, indices in needed.items():
        extra.extend(store.get_by_indices(book_id, sorted(indices)))

    merged = chunks + extra
    merged.sort(key=lambda c: (c["book_id"], c["chunk_index"]))
    return merged


async def _hyde(query: str) -> str:
    """クエリへの仮説回答を ChatClient で生成して返す（HyDE）。"""
    content = ""
    try:
        client = _make_chat_client(timeout=30.0)
        async for token in client.stream_chat(
            [{"role": "user", "content": f"次の質問に対して簡潔に答えてください: {query}"}]
        ):
            content += token
    except Exception as e:  # noqa: BLE001
        logger.warning(f"HyDE 生成失敗: {e}, クエリにフォールバック")
        return query
    return content if content else query


def _hybrid_rrf(
    query: str,
    vec_chunks: list[dict],
    store: PgVectorStore,
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


def _make_chat_client(timeout: float | None = None) -> ChatClient:
    """設定に基づいてChatClientを作成する。"""
    from workers.chat.ollama_chat import OllamaChatClient

    if config.CHAT_BACKEND == "ollama":
        kwargs: dict[str, float] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return OllamaChatClient(config.OLLAMA_HOST, config.CHAT_MODEL, **kwargs)
    # 将来: bedrock 実装を追加
    raise ValueError(f"Unknown CHAT_BACKEND: {config.CHAT_BACKEND}")


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

_NO_RESULTS_MESSAGE = "参考文献の中に、ご質問に該当する情報が見つかりませんでした。"


def _validate_chat_input(body: dict) -> tuple[str | None, int]:
    """
    /api/chat の入力を検証する。

    Returns:
        (error_message, status_code) の tuple。エラーがなければ (None, 200)。
    """
    # query の検証
    query = (body.get("query") or "").strip()
    if not query:
        return ("query は必須です", 400)

    # top_k の検証
    try:
        top_k = int(body.get("top_k", 5))
        if top_k < 1 or top_k > 100:
            return ("top_k は 1～100 の範囲で指定してください", 422)
    except (ValueError, TypeError):  # fmt: skip
        return ("top_k は整数である必要があります", 422)

    # history の検証
    history: list[dict] = body.get("history", [])
    if not isinstance(history, list):
        return ("history はリストである必要があります", 422)

    if len(history) > config.MAX_HISTORY_MESSAGES:
        return (
            f"history は最大 {config.MAX_HISTORY_MESSAGES} 件までです（{len(history)} 件）",
            422,
        )

    for i, msg in enumerate(history):
        if not isinstance(msg, dict):
            return (f"history[{i}] は辞書である必要があります", 422)

        # role の検証
        if "role" not in msg:
            return (f"history[{i}] に role が指定されていません", 422)

        role = msg.get("role")
        if role not in ("user", "assistant"):
            return (
                f'history[{i}] の role は "user" または "assistant" である必要があります',
                422,
            )

        # content の検証
        if "content" not in msg:
            return (f"history[{i}] に content が指定されていません", 422)

        if not msg.get("content"):
            return (f"history[{i}] の content は空でない必要があります", 422)

    # ここまでで history の各要素が dict かつ role/content を持つことが保証されている
    total_chars = sum(len(str(msg["content"])) for msg in history)
    if total_chars > config.MAX_HISTORY_TOTAL_CHARS:
        return (
            f"history の合計文字数は最大 {config.MAX_HISTORY_TOTAL_CHARS} 文字までです"
            f"（{total_chars} 文字）",
            422,
        )

    return (None, 200)


async def chat(request: Request) -> StreamingResponse | JSONResponse:
    """RAG チャット: クエリ埋め込み → pgvector 検索 → Ollama 生成（SSE ストリーム）。"""
    body = await _parse_json_body(request)
    if body is None:
        return _invalid_json_response()

    # 入力検証
    error_msg, status_code = _validate_chat_input(body)
    if error_msg is not None:
        return JSONResponse({"detail": error_msg}, status_code=status_code)

    query = (body.get("query") or "").strip()
    history: list[dict] = body.get("history", [])
    top_k: int = int(body.get("top_k", 5))
    persona: str = body.get("persona", "")
    lang: str = body.get("lang", "ja")
    book_id_raw = body.get("book_id")
    if book_id_raw is not None and not isinstance(book_id_raw, str):
        return JSONResponse({"detail": "book_id は文字列である必要があります"}, status_code=400)
    book_id: str | None = book_id_raw or None

    # HyDE: クエリから仮説回答を生成（非同期）
    search_query = query
    if config.HYDE_ENABLED:
        search_query = await _hyde(query)

    # 検索実行（同期・スレッド）
    loop = asyncio.get_running_loop()
    try:
        # _retrieve の署名は (query, top_k, book_id) を使用
        chunks = await loop.run_in_executor(None, _retrieve, search_query, top_k, book_id)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Retrieval error: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "An error occurred while retrieving documents. Please try again."},
            status_code=500,
        )

    if config.SCORE_THRESHOLD_ENABLED and not chunks:
        # スコア閾値未満で全チャンクが除外された場合、LLM を呼ばず幻覚を防ぐ
        async def no_results_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
            msg = json.dumps({"type": "token", "content": _NO_RESULTS_MESSAGE}, ensure_ascii=False)
            yield f"data: {msg}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(no_results_stream(), media_type="text/event-stream")

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

    async def event_stream() -> AsyncIterator[str]:
        src_msg = json.dumps({"type": "sources", "sources": sources}, ensure_ascii=False)
        yield f"data: {src_msg}\n\n"
        try:
            client = _make_chat_client()
            async for token in client.stream_chat(messages):
                msg = json.dumps({"type": "token", "content": token}, ensure_ascii=False)
                yield f"data: {msg}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except RuntimeError as e:
            logger.error(f"Chat generation error: {e}", exc_info=True)
            error_msg = "An error occurred while generating a response. Please try again."
            msg = json.dumps(
                {"type": "error", "message": error_msg},
                ensure_ascii=False,
            )
            yield f"data: {msg}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Chat error: {e}", exc_info=True)
            msg = json.dumps(
                {"type": "error", "message": "An error occurred. Please try again."},
                ensure_ascii=False,
            )
            yield f"data: {msg}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# JSON ボディの値は外部入力で型が動的に定まらないため Any を許容する。
_JsonBody = dict[str, Any]


async def _parse_json_body(request: Request) -> _JsonBody | None:
    """リクエストボディを JSON dict としてパースする。

    不正な JSON、または JSON として妥当でも dict でないボディ（null・配列・文字列など）は
    None を返す（呼び出し側で 400 エラーレスポンスに変換する）。
    """
    try:
        body = await request.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _invalid_json_response() -> JSONResponse:
    """不正な JSON ボディに対する 400 レスポンスを返す。"""
    return JSONResponse({"detail": "不正な JSON ボディです"}, status_code=400)


async def presign(request: Request) -> JSONResponse:
    """raw/<filename> への PUT 用 presigned URL を発行する。

    チェック項目:
    - ファイル名の妥当性（パストラバーサル、PDF 拡張子）
    - 既存オブジェクトの有無（上書き防止）
    - Content-Length が上限以下か（500MB）
    """
    body = await _parse_json_body(request)
    if body is None:
        return _invalid_json_response()
    try:
        name = _safe_name(body.get("filename", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    if not name.lower().endswith(".pdf"):
        return JSONResponse({"detail": "PDF ファイルのみ対応しています"}, status_code=400)

    # Content-Length チェック
    content_length = body.get("content_length")
    if content_length is not None and content_length > MAX_UPLOAD_SIZE:
        max_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
        return JSONResponse(
            {"detail": f"ファイルサイズが大きすぎます。上限は {max_mb}MB です。"},
            status_code=413,
        )

    key = f"{RAW_PREFIX}{name}"

    # 既存オブジェクト確認（上書き防止）
    try:
        if _object_exists(config.S3_BUCKET, key):
            return JSONResponse(
                {"detail": "このファイル名は既に存在します。別の名前で再試行してください。"},
                status_code=409,
            )
    except ClientError as e:
        # S3 API エラー
        logger.error(f"S3 error checking object existence for key={key}: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "Failed to verify file availability. Please try again."},
            status_code=503,
        )
    except Exception as e:  # noqa: BLE001
        # その他のエラー
        logger.error(
            f"Unexpected error checking object existence for key={key}: {e}", exc_info=True
        )
        return JSONResponse(
            {"detail": "An error occurred. Please try again."},
            status_code=500,
        )

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

    チェック項目:
    - title/author の必須入力
    - Content-Length が上限以下か（500MB）
    """
    body = await _parse_json_body(request)
    if body is None:
        return _invalid_json_response()
    title = (body.get("title") or "").strip()
    author = (body.get("author") or "").strip()
    if not title or not author:
        return JSONResponse({"detail": "title と author は必須です"}, status_code=400)

    # Content-Length チェック
    content_length = body.get("content_length")
    if content_length is not None and content_length > MAX_UPLOAD_SIZE:
        max_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
        return JSONResponse(
            {"detail": f"ファイルサイズが大きすぎます。上限は {max_mb}MB です。"},
            status_code=413,
        )

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
    except ClientError as e:
        # S3 API エラー（ファイルが見つからないなど）
        logger.error(f"S3 error for book_id={book_id}: {e}", exc_info=True)
        status_code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 503)
        return JSONResponse(
            {"detail": "Failed to save metadata. Please try again."},
            status_code=status_code,
        )
    except ConnectionError as e:
        # S3 接続エラー
        logger.error(f"S3 connection error for book_id={book_id}: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "Failed to save metadata. Please try again."},
            status_code=503,
        )
    except Exception as e:  # noqa: BLE001
        # その他のエラー
        logger.error(f"Unexpected error saving metadata for book_id={book_id}: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "An error occurred. Please try again."},
            status_code=500,
        )
    return JSONResponse({"book_id": book_id})


async def ingest(request: Request) -> JSONResponse:
    """取り込みパイプライン（extract→chunk→embed）をバックグラウンドで起動する。"""
    body = await _parse_json_body(request)
    if body is None:
        return _invalid_json_response()
    try:
        book_id = _safe_name(body.get("book_id", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    _set_status(book_id, "pending")
    tasks = BackgroundTasks()
    tasks.add_task(_run_pipeline, book_id)
    return JSONResponse({"book_id": book_id, "status": "pending"}, background=tasks)


def ingest_status(request: Request) -> JSONResponse:
    """取り込みステータスを返す（永続ストアから）。"""
    book_id = request.path_params["book_id"]
    try:
        store = _get_status_store()
        try:
            status_record = store.get_current_status(book_id)
            if status_record:
                updated_at = status_record.get("updated_at")
                updated_at_str = (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
                )
                return JSONResponse(
                    {
                        "status": status_record.get("status", "unknown"),
                        "chunks_processed": status_record.get("chunks_processed", 0),
                        "error": status_record.get("error_msg"),
                        "updated_at": updated_at_str,
                    }
                )
            return JSONResponse(
                {
                    "status": "unknown",
                    "chunks_processed": 0,
                    "error": None,
                    "updated_at": None,
                }
            )
        finally:
            if store is not _status_store:
                store.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to retrieve status for book_id={book_id}: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "Failed to retrieve status. Please try again."},
            status_code=500,
        )


def ingest_status_history(request: Request) -> JSONResponse:
    """取り込みステータスの履歴を返す（全トランジション）。"""
    book_id = request.path_params["book_id"]
    try:
        store = _get_status_store()
        try:
            history = store.get_status_history(book_id)
            if not history:
                return JSONResponse([])

            def format_record(record: dict) -> dict:
                created_at = record.get("created_at")
                return {
                    "status": record.get("status"),
                    "chunks_processed": record.get("chunks_processed", 0),
                    "error": record.get("error_msg"),
                    "created_at": (
                        created_at.isoformat() if isinstance(created_at, datetime) else created_at
                    ),
                }

            formatted_history = [format_record(record) for record in history]
            return JSONResponse(formatted_history)
        finally:
            if store is not _status_store:
                store.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to retrieve status history for book_id={book_id}: {e}", exc_info=True)
        return JSONResponse(
            {"detail": "Failed to retrieve status history. Please try again."},
            status_code=500,
        )


def check_database_connectivity() -> bool:
    """PostgreSQL への接続をテストする。接続可能なら True、失敗したら False を返す。"""
    try:
        from workers.embed.pgvector_store import PgVectorStore

        vec_store = PgVectorStore(config.database_url())
        try:
            vec_store.close()
            return True
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


def check_embedding_service() -> bool:
    """Ollama 埋め込みサービスへの接続をテストする。接続可能なら True、失敗したら False を返す。"""
    try:
        resp = httpx.get(f"{config.OLLAMA_HOST}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def list_books(request: Request) -> JSONResponse:
    """取り込み済み（pgvector 格納済み）の書籍一覧を返す。

    チャット UI の書籍選択・アップロード画面の書籍管理に使用する。
    """
    from workers.embed.pgvector_store import PgVectorStore

    try:
        store = PgVectorStore(config.database_url())
        try:
            books = store.list_books()
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list books: {e}", exc_info=True)
        return JSONResponse({"detail": "Failed to list books. Please try again."}, status_code=500)
    return JSONResponse(books)


def delete_book(request: Request) -> JSONResponse:
    """book_id に紐づく全データ（pgvector のチャンク + S3 の raw/normalized/chunks + 取り込み
    ステータス履歴）を削除する。

    取り込み中（pending/processing）の書籍は _run_pipeline との競合を避けるため削除を拒否する。
    """
    from workers.embed.pgvector_store import PgVectorStore
    from workers.storage import ObjectStore

    try:
        book_id = _safe_name(request.path_params["book_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)

    try:
        status_store = _get_status_store()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to open status store for book_id={book_id}: {e}", exc_info=True)
        return JSONResponse({"detail": "Failed to delete book. Please try again."}, status_code=500)

    try:
        try:
            current_status = status_store.get_current_status(book_id)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to check status before deleting book_id={book_id}: {e}", exc_info=True
            )
            return JSONResponse(
                {"detail": "Failed to delete book. Please try again."}, status_code=500
            )

        if current_status and current_status.get("status") in ("pending", "processing"):
            return JSONResponse(
                {"detail": "取り込み中の書籍は削除できません。完了してから再試行してください。"},
                status_code=409,
            )

        try:
            store = PgVectorStore(config.database_url())
            try:
                deleted_chunks = store.delete_book(book_id)
            finally:
                store.close()
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to delete pgvector chunks for book_id={book_id}: {e}", exc_info=True
            )
            return JSONResponse(
                {"detail": "Failed to delete book. Please try again."}, status_code=500
            )

        # pgvector 削除後にステータス履歴も削除する（失敗しても致命的ではないためログのみ）
        try:
            status_store.delete_status(book_id)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to delete status history for book_id={book_id}: {e}", exc_info=True
            )
    finally:
        if status_store is not _status_store:
            status_store.close()

    try:
        ObjectStore().delete_book_files(book_id)
    except Exception as e:  # noqa: BLE001
        # pgvector の削除は既に成功しているため、部分成功として明示的に報告する
        logger.error(
            f"Deleted pgvector chunks but failed to delete object storage files "
            f"for book_id={book_id}: {e}",
            exc_info=True,
        )
        return JSONResponse(
            {
                "detail": (
                    "検索データは削除されましたが、ストレージ内のファイル削除に失敗しました。"
                    "もう一度削除を実行してください。"
                ),
                "book_id": book_id,
                "deleted_chunks": deleted_chunks,
            },
            status_code=502,
        )

    return JSONResponse({"book_id": book_id, "deleted_chunks": deleted_chunks})


def health(request: Request) -> JSONResponse:
    """ヘルスチェックエンドポイント。DB と Ollama の接続状態をチェックする。"""
    db_ok = check_database_connectivity()
    embedding_ok = check_embedding_service()

    status_code = 200 if (db_ok and embedding_ok) else 503
    status = "healthy" if (db_ok and embedding_ok) else "unhealthy"

    response = {
        "status": status,
        "services": {
            "db": "ok" if db_ok else "unreachable",
            "embedding": "ok" if embedding_ok else "unreachable",
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }

    return JSONResponse(response, status_code=status_code)


app = Starlette(
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/presign", presign, methods=["POST"]),
        Route("/api/meta", save_meta, methods=["POST"]),
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/ingest/{book_id}/status", ingest_status, methods=["GET"]),
        Route("/api/ingest/{book_id}/status/history", ingest_status_history, methods=["GET"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/books", list_books, methods=["GET"]),
        Route("/api/books/{book_id}", delete_book, methods=["DELETE"]),
        # 静的フロント（最後にマウント。html=True で / に index.html を返す）
        Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"),
    ]
)

# 認証ミドルウェアをマウント
app.add_middleware(AuthMiddleware)
