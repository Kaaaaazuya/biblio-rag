# 0010. E2E テスト戦略（パイプライン=pytest / WebUI=Playwright）

- 状態: Accepted
- 日付: 2026-06-17
- 関連: [design.md](../design.md)、[ADR 0008](0008-object-storage-minio.md)、[quickstart](../quickstart.md)

## コンテキスト

縦串（抽出→チャンク→埋め込み→pgvector→検索）と WebUI アップロードを手で通して検証していたが、
**再現可能な資産**として残し回帰検知に使いたい。検証対象は性質の違う2層に分かれる:

- **取り込みパイプライン**（バックエンド Python・実インフラ MinIO/pgvector/Ollama が必要）。
- **WebUI アップロード**（ブラウザから presigned URL で MinIO へ直接 PUT する経路）。

制約: 書籍本文はコミットしない（著作権）。普段の `uv run pytest`（高速・36件）は緑のままにしたい。
npm は使わない方針。Python は 3.14.0rc1（過去に pydantic 系が動かなかった実績あり）。

## 決定

E2E を **2層**に分け、いずれも**マーカーで既定除外**・**実データを汚さない隔離**とする。

1. **パイプライン E2E = pytest**（[tests/test_pipeline_e2e.py](../../tests/test_pipeline_e2e.py)、`-m e2e`）
   - 入力は公開フィクスチャ [tests/fixtures/sample_book.pdf](../../tests/fixtures/sample_book.pdf)（実書籍は使わない）。
   - `ObjectStore.put_file→get_bytes → extract → chunk → embed_and_store(PgVectorStore) → search` を全段実行。
   - インフラ未起動なら **skip**（fail にしない）。専用 `book_id=e2e_*` で隔離し、teardown で
     DB 行（`delete_book`）と MinIO オブジェクトを削除。

2. **WebUI E2E = Playwright**（[tests/test_webui_e2e.py](../../tests/test_webui_e2e.py)、`-m webui`）
   - `uvicorn webui.server:app` をサブプロセス起動し、chromium でアップロード UI を操作 →
     presigned PUT → MinIO `raw/` 着地と `*.meta.json` 保存を検証。
   - **Playwright は Python パッケージを uv で導入**（`pytest-playwright`・**npm 不要**）。
     ブラウザ本体は `uv run playwright install chromium`（初回のみ）。
   - MinIO 未起動なら skip。一意名でアップロードし teardown で削除。

3. **既定スイートから除外**: `pyproject.toml` の `addopts = "-m 'not e2e and not webui'"`。
   明示実行は `uv run pytest -m e2e` / `uv run pytest -m webui`。

## 理由

- 2層は守備範囲が違う。Playwright はブラウザ層のみで、パイプライン本体は見られない → pytest と併用。
- フィクスチャ入力なら git/CI に載せられ、著作権・データ規約（books/ 非コミット）に抵触しない。
  実書籍での品質目視は別途**手動評価**として残す（役割分担）。
- マーカー除外で普段の開発ループ（`uv run pytest`）は高速・無依存のまま。
- Playwright-Python は 3.14.0rc1 で依存解決・実行ともに動作確認済み（`greenlet==3.5.1`）。npm 方針にも反しない。

## 結果

- 良い点: 「UI アップロード → 検索可能」までを2コマンドで再検証でき、回帰検知になる。隔離・後始末で実データ安全。
- 悪い点: ブラウザバイナリ（chromium ヘッドレス ~90MB）という重い前提が増える。E2E は実インフラ前提。
- 継ぎ目: 2nd ステージ（AWS 化）では接続先を差し替えれば pytest E2E はそのまま流用できる。
  WebUI に取り込みトリガが付いたら、webui E2E をアップロード→検索まで延伸できる。
