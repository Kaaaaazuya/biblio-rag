# テスト戦略

テストは「**実インフラ（MinIO / pgvector / Ollama / ブラウザ）を要するか**」で 3 層に分ける。
要らない層（単体・結合）は既定で常時実行、要る層（E2E）はオプトイン。設計の経緯は
[ADR 0010](adr/0010-e2e-testing-strategy.md)。

## 3 層の全体像

| 層 | ツール | 件数 | 実インフラ | 実行 | 担保するもの |
|---|---|---|---|---|---|
| 単体 (UT) | `pytest` + monkeypatch (fake) | 30 | 不要 | `uv run pytest` | 各層ロジックの正しさ |
| 結合 | `pytest` + Starlette TestClient | 6 | 不要（プロセス内） | `uv run pytest` | WebUI バックエンド API の契約 |
| E2E | `pytest`（実インフラ）/ `pytest-playwright` | 2 | 必要 | `-m e2e` / `-m webui` | 全鎖が実環境で通ること |

## 実行モデル

```bash
uv run pytest            # 単体 + 結合（36件・高速・無依存）= 既定
uv run pytest -m e2e     # パイプライン E2E（要スタック）
uv run pytest -m webui   # WebUI E2E（要スタック + 初回 playwright install）
```

- 既定で E2E を除外する設定: `pyproject.toml` の `addopts = "-m 'not e2e and not webui'"`。
- E2E は実インフラ未起動なら **fail ではなく skip**。専用 `book_id` / 一意名で隔離し、teardown で
  DB 行・MinIO オブジェクトを削除するため実データを汚さない。
- 初回のみ: `uv run playwright install chromium`。

## 各テストが担保するもの

### 単体 (UT) — pytest, fake で隔離
| ファイル | 件数 | 担保 |
|---|---|---|
| `tests/test_extract.py` | 7 | 見出しの相対判定 / ヘッダ・フッタ除去 / 段落復元 |
| `tests/test_chunk.py` | 10 | 分割境界 / 必須メタの付与 / 異常系 |
| `tests/test_embed.py` | 5 | バッチ分割 / 次元検証 / 格納件数（httpx は monkeypatch） |
| `tests/test_search.py` | 3 | 整形 / スニペット / クラム表示 |
| `tests/test_atr_gate.py` | 4 | high+ 判定 / 誤検知（file+rule_id）除外 |
| `tests/test_upload.py` | 1 | meta サイドカー書き込み |

### 結合 — pytest + Starlette TestClient（サーバー/ブラウザ無し）
| ファイル | 件数 | 担保 |
|---|---|---|
| `tests/test_webui_api.py` | 6 | `/api/presign`（署名発行・ファイル名/拡張子バリデーション）と `/api/meta`（保存・必須チェック）の契約。presigned URL 生成は boto3 のみでライブ MinIO 不要 |

### E2E — 実インフラ（オプトイン）
| ファイル | マーカー | ツール / 実インフラ | 担保 |
|---|---|---|---|
| `tests/test_pipeline_e2e.py` | `e2e` | pytest / MinIO + pgvector + Ollama(bge-m3) | 抽出 → チャンク → 埋め込み → pgvector → 検索 の全鎖が通り、検索が当該書籍を返す |
| `tests/test_webui_e2e.py` | `webui` | pytest-playwright(chromium) / uvicorn + MinIO | 実ブラウザで UI アップロード → presigned PUT → MinIO `raw/` 着地 → meta 保存 |

## 設計の意図

- **担保の重なりを避ける**: E2E は「全鎖が通る」ことだけを薄く担保し、細かいロジックの網羅は UT に任せる
  （E2E は各層 1 件に絞る）。
- **名前は実態に合わせる**: API の結合テストは `test_webui_api.py`、ブラウザ E2E は `test_webui_e2e.py`、
  パイプライン E2E は `test_pipeline_e2e.py`。
- **CI 想定**: 結合までは常時、E2E は実インフラのある job で回す。
