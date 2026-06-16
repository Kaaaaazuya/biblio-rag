# biblio-rag — 日本語書籍 RAG パイプライン

購入済みの日本語 PDF 書籍を入力に、RAG の検索対象となるベクトルインデックスを構築する**取り込みパイプライン**。
`PDF → 抽出 → チャンク → 埋め込み → pgvector 格納 → 最小検索` までを対象とする（回答生成 LLM はスコープ外）。

- **開発**: ローカル完結（無料）— Ollama `bge-m3` + Docker pgvector
- **本番**: AWS（ECS Fargate + Lambda + SQS + Aurora pgvector + Bedrock Titan V2）※2nd ステージ
- 詳細設計は [`docs/design.md`](docs/design.md)、意思決定の経緯は [`docs/adr/`](docs/adr/) を参照

> **現在のステータス: MVP 完了（T1〜T5）。** ローカルで PDF→抽出→チャンク→埋め込み→pgvector→検索の縦串が通る。非同期化・AWS 化は 2nd ステージ。

> 🚀 **すぐ動かしたい人は [docs/quickstart.md](docs/quickstart.md)** へ（同梱の著作権フリーPDFで検索結果まで一気通貫）。

---

## 必要なツール（前提）

| ツール | 用途 | 確認 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python 依存管理（**npm は使わない方針**） | `uv --version` |
| Python 3.14 | uv が自動取得（`.python-version` で固定） | — |
| Docker | pgvector / Ollama を起動（T1〜） | `docker --version` |
| [Ollama](https://ollama.com/) | 埋め込みモデル `bge-m3` の API 提供（T4〜） | `ollama --version` |
| [gitleaks](https://github.com/gitleaks/gitleaks) | コミット前の秘密情報検出（`brew install gitleaks`） | `gitleaks version` |
| [pre-commit](https://pre-commit.com/) | フック管理（`uv tool install pre-commit`） | `pre-commit --version` |

---

## セットアップ

```bash
# 1) 依存を同期（Python 3.14 を uv が用意し .venv を作成）
uv sync

# 2) 環境変数ファイルを用意（実値はコミットされない）
cp .env.example .env

# 3) 秘密情報ブロックのフックを有効化（必須）
uv tool install pre-commit   # 未導入の場合
brew install gitleaks        # 未導入の場合
pre-commit install
pre-commit run --all-files   # 初回フルスキャンで動作確認
```

### 開発スタックの起動（T1）

```bash
# DB(pgvector)・Ollama・MinIO(S3互換) を起動（スキーマ/バケットは初回に自動適用）
docker compose -f docker/docker-compose.yml up -d

# 埋め込みモデルを取得（約 1.2GB・初回のみ）※コンテナ内の Ollama に対して実行
docker compose -f docker/docker-compose.yml exec ollama ollama pull bge-m3

# 動作確認（1024 次元のベクトルが返る）
curl http://localhost:11434/api/embed -d '{"model":"bge-m3","input":"テスト"}'

# MinIO コンソール: http://localhost:9001 （minioadmin / minioadmin）でアップロード結果を確認可
```

> **⚠️ macOS のポート衝突注意:** brew 版 Ollama を起動していると 11434 が衝突する。docker 版を使う間は native を止める（`brew services stop ollama` またはアプリ終了）。
> **⚠️ パフォーマンス:** Docker 内 Ollama は Metal GPU を使えず CPU 動作。速度を優先したい場合は native Ollama に切り替える構成も可。

停止 / 破棄:
```bash
docker compose -f docker/docker-compose.yml down        # 停止（データは保持）
docker compose -f docker/docker-compose.yml down -v     # ボリューム含め破棄（スキーマ再適用したい時）
```

---

## パイプライン実行（MVP）

PDF は **MinIO(S3) の `raw/`** にアップロードし、`title`/`author` のサイドカー JSON を用意して直列実行する。
（`book_id` はファイル名 stem。`books/`（normalized/chunks/meta）は `.gitignore` 済みでコミットされない）

```bash
# 0) スタック起動 + モデル取得（上記「開発スタックの起動」を実施済みとする）

# 1) PDF を S3(MinIO) にアップロード（または MinIO コンソールから）。必須メタデータも用意
uv run python -m workers.upload your_book.pdf            # → s3://biblio/raw/your_book.pdf
echo '{"title": "書名", "author": "著者名"}' > books/your_book.meta.json

# 2) ① 抽出: S3(raw/) の PDF → books/normalized/*.md
uv run python -m workers.extract

# 3) ② チャンク: books/normalized/*.md → books/chunks/*.jsonl
uv run python -m workers.chunk

# 4) ③ 埋め込み + 格納: books/chunks/*.jsonl → pgvector
uv run python -m workers.embed

# 5) 検索（出典つきで上位チャンクを表示）
uv run python -m workers.search "調べたいこと" --top-k 5
```

## セキュリティ / データ取り扱いルール（厳守）

このリポジトリは**パブリック公開前提**。以下を機械的・運用的に守る。

- **書籍データはコミットしない。** PDF 原本(`books/raw/`)だけでなく、抽出本文(`books/normalized/`)・チャンク(`books/chunks/`)も**書籍本文を含む**ため `.gitignore` で `books/` ツリー全体を除外している。
- **「正本」= git ではなくディスク/S3。** `chunks/*.jsonl` 等の「正本」は再チューニング用の元データという意味で、保管先はローカルディスク（開発）/ S3（本番）。git では管理しない。
- **コミットしてよい本文は著作権フリー物のみ** — 青空文庫等を `tests/fixtures/` に置く。
- **AWS 実キーをローカルに置かない。** 開発は Ollama + Docker pgvector で完結するためダミーで足りる。本番 DB 認証は Secrets Manager。
- `.env` は `.gitignore` 済み。共有は `.env.example`（キー名のみ）で。
- **gitleaks + `check-added-large-files`** で秘密情報・大容量ファイルの誤コミットを二重ブロック。

### GitHub 公開時チェックリスト（リモート作成時に実施）

- [ ] リポジトリの **Secret Scanning** を有効化
- [ ] **Push Protection** を有効化（ローカルフックすり抜けの最終防壁）
- [ ] 公開前に `git log` / 履歴に書籍本文・キーが混入していないか確認

---

## 環境メモ（設計上の注意点）

| 項目 | メモ |
|---|---|
| Python 3.14 固定 | システム Python(3.9) は EOL 間近のため使わない。uv が 3.14 を管理。 |
| ライセンス | 本リポジトリのコードは **MIT**。ただし抽出に使う **PyMuPDF は AGPL**（再配布時は各依存のライセンスに従う。ローカル利用は問題なし）。 |
| 埋め込み次元 | 開発 bge-m3 / 本番 Titan V2 ともに **1024 次元**でスキーマ共通化。ただし意味空間は別物なので本番移行時は再埋め込みが必要。 |
| gitleaks フック | 公式フックは `go build` を伴うため、brew 版バイナリを `language: system` で呼ぶ構成にしている。 |

---

## 開発ルール

- コミットメッセージは **Conventional Commits** に統一。規約は [`docs/commit-convention.md`](docs/commit-convention.md)、実作業は `/commit` スキルが支援する。
- リンター/フォーマッターは **Ruff**。pre-commit で自動実行される。手動実行は以下。

```bash
uv run ruff check --fix    # lint（自動修正つき）
uv run ruff format         # フォーマット
uv run pytest              # テスト
```

## ディレクトリ構成（目標）

```
books/            # 書籍データ（.gitignore・コミット禁止）
  raw/            #   （現在は未使用。原本 PDF は MinIO/S3 の raw/ に置く）
  normalized/     #   ① 抽出済み Markdown（正本・ローカルFS）
  chunks/         #   ② 分割済み JSONL（正本・ローカルFS）
  *.meta.json     #   書籍メタ（title/author・ローカルFS）
workers/
  extract/        # ① PyMuPDF 抽出
  chunk/          # ② チャンク（サイズ可変）
  embed/          # ③ 埋め込み + 格納（Embedder/VectorStore 抽象化）
infra/db/         # pgvector スキーマ（開発/本番共通）
docker/           # docker-compose.yml（postgres + Ollama）
tests/fixtures/   # 著作権フリーのテスト用データ（コミット可）
docs/
  design.md           # 確定した設計
  commit-convention.md # コミットメッセージ規約（正本）
  adr/                # アーキテクチャ決定記録
```

## ライセンス

[MIT](LICENSE)
