# CLAUDE.md — biblio-rag 開発の常時ルール

日本語書籍の RAG **取り込みパイプライン**（PDF→抽出→チャンク→埋め込み→pgvector→最小検索）。
回答生成 LLM はスコープ外。詳細は [README.md](README.md) / [docs/design.md](docs/design.md)（配置予定）。

## 現在のステージ

- **MVP を作っている**: AWS 抜き・1冊・直列スクリプトで縦串を1本通す（T1〜T5）。
- **MVP では SQS / Lambda / Fargate / LocalStack / AWS を一切使わない。** 非同期化・AWS 化は MVP 完了後の 2nd ステージ（記録のみ）。
- 進捗: MVP 完了（T1〜T5。抽出→チャンク→埋め込み→pgvector→検索が縦串で通る）。次は T6（design.md/ADR 整備）。

## 技術スタック / 実行

- **Python 3.14 を uv で管理。** システム Python / 素の pip は使わない。
  - 実行: `uv run <script>` / 依存追加: `uv add <pkg>` / 同期: `uv sync`。
- **npm は使わない**（方針）。フック類も npm 非依存（pre-commit + gitleaks）。
- リント/整形は **Ruff**: `uv run ruff check --fix` / `uv run ruff format`。テストは `uv run pytest`。pre-commit で自動実行。
- 埋め込み（開発）: Ollama `bge-m3`（`http://localhost:11434/api/embed`・1024次元）。
- ベクトル DB（開発）: pgvector on Docker。スキーマは `VECTOR(1024)`。

## ディレクトリ規約

```
books/{raw,normalized,chunks}/  # 書籍データ。.gitignore 済み・コミット禁止
workers/{extract,chunk,embed}/  # ①②③ の処理コード
infra/db/                       # pgvector スキーマ（開発/本番共通）
docker/                         # docker-compose.yml
tests/fixtures/                 # 著作権フリーのテストデータ（コミット可）
docs/                           # design.md / commit-convention.md / adr/
```

## データ / セキュリティ（厳守）

- **書籍データは絶対にコミットしない。** `books/`（raw/normalized/chunks すべて）は本文を含むため git 管理外。`.gitignore` 済み。
- **「正本」は git ではなくディスク/S3。** `normalized/*.md`・`chunks/*.jsonl` は再チューニング用の元データという意味の正本で、保管先はローカルディスク（開発）/ S3（本番）。
- コミット可能な本文は `tests/fixtures/` の著作権フリー物のみ（青空文庫等）。
- **キー・秘密情報をコミットしない。** `.env` は `.gitignore`、共有は `.env.example`（キー名のみ）。AWS 実キーは開発では不要（ダミーで足りる）。本番 DB 認証は Secrets Manager。

## インターフェース契約（③ 埋め込み層）

開発/本番で実装を差し替えるため、以下の抽象を守る。

```python
class Embedder(ABC):
    def embed(self, texts: list[str]) -> list[list[float]]: ...   # 1024次元

class VectorStore(ABC):
    def upsert(self, chunks: list[dict], vectors) -> None: ...
    def search(self, query_vector, top_k: int) -> list[dict]: ...
```

- 開発: `OllamaEmbedder` / `PgVectorStore`。本番: `BedrockEmbedder` / `PgVectorStore`(Aurora・同一実装)。
- 次元は開発/本番とも 1024 で統一（スキーマ共通化）。ただし意味空間は別物なので本番移行時は再埋め込みが必要。

## メタデータ（チャンク/DB 共通の列）

`book_id` / `title` / `author` / `chapter` / `section` / `page` / `text`

## コミット

- **Conventional Commits 準拠。** 規約の正本は [docs/commit-convention.md](docs/commit-convention.md)、実作業は `/commit` スキル。
- pre-commit フック（gitleaks 等）を必ず通す。**`--no-verify` で迂回しない。**
- コミット末尾に `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` を付ける。

## テスト

- テスト用 PDF は著作権フリー（青空文庫等）を `tests/fixtures/` に置く。実書籍は使わない。
