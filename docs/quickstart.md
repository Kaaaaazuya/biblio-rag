# クイックスタート（動かしてみる）

ゼロから **PDF → 抽出 → チャンク → 埋め込み → pgvector → 検索** を一通り動かす手順。
自分の書籍が無くても、**同梱の著作権フリーPDF**（`tests/fixtures/sample_book.pdf`）で最後まで試せる。

> コマンドはリポジトリ root で実行する。Python は uv が管理（システム Python は使わない）。

---

## 0. 初回セットアップ（1回だけ）

```bash
uv sync                       # Python 3.14 + 依存をインストール（.venv 作成）
cp .env.example .env          # 設定（ローカル用・コミットされない）
```

前提ツール: `docker` / `uv` / `ollama`(任意) / `gitleaks`・`pre-commit`(コミットする場合)。
詳細は [README](../README.md#必要なツール前提)。

---

## 1. 開発スタックを起動

pgvector(PostgreSQL)・Ollama・MinIO(S3互換) を Docker で起動し、埋め込みモデルを取得する。

```bash
# DB + Ollama + MinIO 起動（スキーマと S3 バケットは初回に自動作成）
docker compose -f docker/docker-compose.yml up -d

# 埋め込みモデル bge-m3 を取得（約1.2GB・初回のみ・コンテナ内へ）
docker compose -f docker/docker-compose.yml exec ollama ollama pull bge-m3

# 動作確認（1024次元が返れば OK）
curl -s http://localhost:11434/api/embed -d '{"model":"bge-m3","input":"テスト"}' | head -c 80; echo
```

> 📦 **MinIO コンソール**: http://localhost:9001 （`minioadmin` / `minioadmin`）。
> アップロードした PDF（`biblio` バケットの `raw/`）をブラウザで確認できる。

> ⚠️ **macOS のポート衝突**: brew 版 Ollama を起動していると 11434 が衝突する。
> docker 版を使う間は native を止める（`brew services stop ollama` またはアプリ終了）。

---

## 2. 同梱フィクスチャで一気通貫（コピペで試せる）

```bash
# ⓪ 同梱PDFを S3(MinIO) にアップロード（--title/--author で meta も同時作成）
uv run python -m workers.upload tests/fixtures/sample_book.pdf \
  --title "RAG 取り込みパイプライン設計ノート" --author "サンプル著者"

# ① 抽出: S3(raw/) の PDF → books/normalized/sample_book.md
uv run python -m workers.extract

# ② チャンク: books/normalized/*.md → books/chunks/*.jsonl
uv run python -m workers.chunk

# ③ 埋め込み + 格納: books/chunks/*.jsonl → pgvector
uv run python -m workers.embed

# ④ 検索（出典つきで上位チャンクを表示）
uv run python -m workers.search "本番環境への移行で何が変わる" --top-k 3
```

期待される出力（抜粋）:

```
クエリ: "本番環境への移行で何が変わる"  (上位 3 件)

[1] score=0.501  RAG 取り込みパイプライン設計ノート / 第一章 設計の前提 > 1.2 実行基盤の方針
    開発はローカルで完結させ、本番はクラウドへ移行する二段構えとする。…
```

DB に入ったか確認したい場合:

```bash
docker compose -f docker/docker-compose.yml exec db \
  psql -U biblio -d biblio -c "SELECT chunk_index, chapter, section, vector_dims(embedding) FROM chunks ORDER BY chunk_index;"
```

---

## 3. 自分の書籍（PDF）で試す

```bash
# 1) PDF を S3(MinIO) にアップロード（--title/--author で meta も同時作成）
uv run python -m workers.upload /path/to/mybook.pdf --title "書名" --author "著者名"
#    MinIO コンソール(:9001) からアップロードする場合は、別途
#    books/<book_id>.meta.json に {"title":..., "author":...} を用意する

# 2) 抽出→チャンク→埋め込み（引数なしで S3 の raw/ と books/ 配下を一括処理）
uv run python -m workers.extract
uv run python -m workers.chunk      # meta が無い本は警告つきでスキップされる
uv run python -m workers.embed

# 3) 検索
uv run python -m workers.search "調べたいこと" --top-k 5
```

> PDF は S3(MinIO)。`books/`（normalized/chunks/meta）は `.gitignore` 済みでコミットされない（著作権保護）。
> **増分実行**: 再実行すると処理済みはスキップされる。作り直したいときは各コマンドに `--force`（洗い替え）。
> チャンクサイズは可変: `uv run python -m workers.chunk --size 600 --overlap 100`。

---

## 4. 後片付け

```bash
docker compose -f docker/docker-compose.yml down       # 停止（データは保持）
docker compose -f docker/docker-compose.yml down -v    # データも破棄（スキーマを再適用したい時）
```

再開は `docker compose -f docker/docker-compose.yml up -d` だけ（モデル・データはボリュームに残る）。

---

## トラブルシュート

| 症状 | 原因 / 対処 |
|---|---|
| `curl :11434` が応答しない | Ollama 未起動 or ポート衝突。native Ollama を停止して docker 版を使う |
| `bge-m3` が無いと言われる | `docker compose -f docker/docker-compose.yml exec ollama ollama pull bge-m3` |
| DB 接続エラー | スタック未起動。`docker compose ... up -d` 後、`db` が healthy か `... ps` で確認 |
| `S3 に PDF がありません` | `workers.upload` で投入したか確認。MinIO 起動・`biblio` バケット作成済みか（`... logs createbuckets`） |
| S3 接続/認証エラー | `.env` の `S3_ENDPOINT_URL`(=http://localhost:9000)・`AWS_ACCESS_KEY_ID/SECRET`(minioadmin) を確認 |
| 本が `スキップ` される | その本の meta 未整備。`upload` に `--title/--author` を付けるか `books/<book_id>.meta.json` を用意して再実行 |
| 次元不一致エラー | 埋め込みモデルが bge-m3(1024次元)か確認。`.env` の `EMBED_MODEL`/`EMBED_DIM` |
| 検索が空 | 先に ③（`workers.embed`）で格納したか確認 |
