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

pgvector(PostgreSQL) と Ollama を Docker で起動し、埋め込みモデルを取得する。

```bash
# DB + Ollama 起動（スキーマは初回起動時に自動適用）
docker compose -f docker/docker-compose.yml up -d

# 埋め込みモデル bge-m3 を取得（約1.2GB・初回のみ・コンテナ内へ）
docker compose -f docker/docker-compose.yml exec ollama ollama pull bge-m3

# 動作確認（1024次元が返れば OK）
curl -s http://localhost:11434/api/embed -d '{"model":"bge-m3","input":"テスト"}' | head -c 80; echo
```

> ⚠️ **macOS のポート衝突**: brew 版 Ollama を起動していると 11434 が衝突する。
> docker 版を使う間は native を止める（`brew services stop ollama` またはアプリ終了）。

---

## 2. 同梱フィクスチャで一気通貫（コピペで試せる）

```bash
# ① 抽出: PDF → books/normalized/sample_book.md
uv run python -m workers.extract tests/fixtures/sample_book.pdf

# 必須メタデータ（title / author）を用意。book_id はファイル名 = sample_book
echo '{"title": "RAG 取り込みパイプライン設計ノート", "author": "サンプル著者"}' > books/sample_book.meta.json

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
# 1) PDF を配置（例: mybook.pdf → book_id = mybook）
cp /path/to/mybook.pdf books/raw/

# 2) 必須メタデータ（title / author）
echo '{"title": "書名", "author": "著者名"}' > books/mybook.meta.json

# 3) 抽出→チャンク→埋め込み（引数なしで books/ 配下を一括処理）
uv run python -m workers.extract
uv run python -m workers.chunk
uv run python -m workers.embed

# 4) 検索
uv run python -m workers.search "調べたいこと" --top-k 5
```

> `books/` は `.gitignore` 済み。PDF・抽出本文・チャンクはコミットされない（著作権保護）。
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
| `メタデータが見つかりません` | `books/<book_id>.meta.json` に `title`/`author` を用意（必須） |
| 次元不一致エラー | 埋め込みモデルが bge-m3(1024次元)か確認。`.env` の `EMBED_MODEL`/`EMBED_DIM` |
| 検索が空 | 先に ③（`workers.embed`）で格納したか確認 |
