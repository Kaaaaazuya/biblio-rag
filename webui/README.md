# webui — PDF アップロードの足場（S3 + 静的ファイル）

書籍 PDF を **ブラウザから S3（開発は MinIO）へ直接アップロード**するための最小スキャフォールド。

## アーキテクチャ

```
[ブラウザ static/index.html]
   │  1. POST /api/presign  ──────────────▶ [backend: Starlette]  署名を発行
   │ ◀──────────── presigned URL ──────────
   │  2. PUT (PDF 本体) ───────────────────▶ [S3 / MinIO]  raw/<file>.pdf  ※本体はバックエンドを通らない
   │  3. POST /api/meta ───────────────────▶ [backend]  books/<book_id>.meta.json 保存
```

- **ファイル本体はバックエンドを経由しない**（presigned URL でブラウザ→S3 直送）。大きい PDF でもサーバ負荷が小さい。
- バックエンドは「署名発行」と「メタ保存」だけ。**本番では API Gateway + Lambda で署名**に置き換えれば、フロントはそのまま流用できる。
- フレームワークは Starlette（軽量・pydantic 非依存。Python 3.14 でも動く）。

## 起動

```bash
# 1) 事前に開発スタック（MinIO 含む）を起動
docker compose -f docker/docker-compose.yml up -d

# 2) WebUI サーバを起動
uv run uvicorn webui.server:app --reload --port 8000

# 3) ブラウザで開く
open http://localhost:8000
```

PDF・書名・著者を入力して「アップロード」→ `s3://biblio/raw/<file>.pdf` と `books/<book_id>.meta.json` が作られる。
その後、取り込みは CLI で:

```bash
uv run python -m workers.extract   # S3(raw/) → normalized（処理済みはスキップ）
uv run python -m workers.chunk
uv run python -m workers.embed
uv run python -m workers.search "調べたいこと"
```

## エンドポイント

| メソッド | パス | 役割 |
|---|---|---|
| `POST` | `/api/presign` | `{filename}` → raw/ への presigned PUT URL（`{url, key, book_id}`） |
| `POST` | `/api/meta` | `{book_id, title, author}` → `books/<book_id>.meta.json` を保存 |
| `GET` | `/` | 静的アップロード画面 |

## 今は足場（今後の拡張）

- アップロード後に取り込み（extract→chunk→embed）を WebUI から起動／進捗表示。
- メタデータも S3 に保存し、取り込みワーカーをイベント駆動（SQS/Lambda）に。
- 認証・複数ユーザー・一覧/削除 UI。
