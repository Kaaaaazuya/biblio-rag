# Design Doc — 日本語書籍 RAG パイプライン

- ステータス: Draft（非同期アーキテクチャ対応版）
- 作成日: 2026-06-16
- 対象: テキスト埋め込み済み日本語 PDF 書籍
- 方針: SQS 非同期 / 開発ローカル完結 → 本番 AWS

> MVP（T1〜T5）はこの設計のうち **AWS・SQS を除いた直列スクリプト版**を実装したもの。
> 非同期化・AWS 化（本書の 3〜5, 9 章）は 2nd ステージ。判断の経緯は [adr/](adr/) を参照。

---

## 1. 目的とスコープ

購入済みの日本語書籍（PDF）を入力として、RAG の検索対象となるベクトルインデックスを構築する。
書籍1冊の処理に数分〜かかるため、本番は SQS を使った非同期設計を採用する。

**スコープ内**: PDF（テキスト埋め込み済み）からの抽出 / 章・節構造の復元とチャンク分割 /
埋め込みとベクトル DB 格納 / SQS による非同期連携 / 開発（ローカル）・本番（AWS）二段構え。

**スコープ外**: 検索クエリ→回答生成（LLM） / スキャン PDF の OCR / DRM 付き書籍 /
ハイブリッド検索・reranker。

## 2. 設計原則

層を分離し、各層が中間成果物をファイルとして残す。チューニングの大半は②③で回るため、
最も重い①の再実行を防ぐことがコスト最小化の鍵。

| 変えたいもの | 再実行が必要な層 | 再利用できる成果物 |
|---|---|---|
| 抽出ロジック・フォント判定 | ① から | なし |
| チャンクサイズ・overlap | ② から | `normalized/*.md` |
| 埋め込みモデル・DB | ③ のみ | `chunks/*.jsonl` |

実行基盤はフェーズで使い分ける（→ [ADR 0002](adr/0002-execution-platforms.md)）。

## 3. なぜ非同期か — SQS 選定の根拠

各フェーズ（抽出・チャンク・埋め込み）は処理時間がバラバラで、直列だと遅い相がボトルネックになる。
SQS を挟むと各フェーズを独立スケールでき、障害耐性（保持・自動リトライ・DLQ）も得られる。
SNS（1対多）・EventBridge（ルーティング過剰）は本用途にオーバースペック（→ [ADR 0003](adr/0003-messaging-sqs.md)）。

| キュー | 役割 | 消費側 | 可視性TO | DLQ |
|---|---|---|---|---|
| `extract-queue` | PDF → normalized/*.md | Fargate | 15分 | extract-dlq |
| `chunk-queue` | normalized/*.md → chunks/*.jsonl | Lambda | 5分 | chunk-dlq |
| `embed-queue` | chunks/*.jsonl → vectordb | Lambda | 15分 | embed-dlq |

## 4. パイプライン全体像

`S3 がデータのバケツ、SQS がジョブの伝言板`。

```
OP → S3(raw/*.pdf) → extract-queue → [Extract:Fargate] PyMuPDF抽出
   → S3(normalized/*.md) → chunk-queue → [Chunk:Lambda] 分割
   → S3(chunks/*.jsonl) → embed-queue → [Embed:Lambda] Embedder→ベクトル化
   → VectorDB(Aurora pgvector) upsert
```

各キューに DLQ を設置し、規定回数リトライ後に退避＋アラート。ローカルは LocalStack（SQS/S3）で再現。

## 5. コスト最適化 — Fargate ゼロスケール

個人利用かつ散発投入のため、処理がない間は課金を止める（→ [ADR 0004](adr/0004-fargate-zero-scale.md)）。

- ① 抽出 = ECS Fargate `min=0`、Application Auto Scaling を SQS キュー深度に連動（方式A）。
- ②③ = Lambda なので元々アイドル課金なし・SQS が直接トリガー。
- スケール判定: `ApproximateNumberOfMessagesVisible + NotVisible`。≥1 で 0→N、=0 で →0。
- トレードオフ: 投入からタスク起動まで数分のスケール待ち（散発バッチでは許容）。

代替案: B=EventBridge Scheduler（夜間バッチ・最安）、C=Step Functions（順序制御が必要になったら）。

## 6. 開発環境 vs 本番環境

`chunks/*.jsonl` は両環境で同一。差異は**埋め込みモデルと接続先の2点だけ**。

| 項目 | 開発（ローカル） | 本番（AWS） |
|---|---|---|
| メッセージキュー | LocalStack SQS | Amazon SQS |
| ストレージ | LocalStack S3 / ローカルFS | Amazon S3 |
| ① 抽出 実行 | Docker コンテナ | ECS Fargate（min=0） |
| ②③ 実行 | LocalStack Lambda / コンテナ | Lambda（SQSトリガー） |
| 埋め込み | Ollama `bge-m3`（1024次元） | Bedrock Titan V2（1024次元） |
| ベクトル DB | pgvector on Docker | Aurora PostgreSQL + pgvector |
| コスト | ¥0 | 従量課金 |

次元を 1024 で揃えるとスキーマ（`VECTOR(1024)`）・インデックス定義を変えずモデルだけ差し替えられる。
ただし**意味空間はモデルごとに別物**なので、本番移行時は `chunks/*.jsonl` から再埋め込みする
（→ [ADR 0006](adr/0006-local-to-aws.md)）。

## 7. 各層の詳細設計

### ① 抽出（Extract Worker）
- `page.get_text("blocks")` / `"dict"` で読み順を安定化（`"text"` は段組み・脚注で乱れる）。
- ヘッダ/フッタ（ページ番号・繰り返し書名）を座標・パターンで除去。
- 改行正規化（段落内改行を結合・空行を段落区切り）。
- 見出し検出: 最頻フォントサイズ=本文、それより大きい=見出しの相対判定＋「第◯章」併用 → `#`/`##`。

### ② チャンク（Chunk）
- 文字数ベース（400〜600字・設定可変）＋ overlap 80字、句点「。」優先。
- 見出し階層を prefix として本文頭に付与（検索精度向上）。
- 分割戦略の選択経緯は [ADR 0007](adr/0007-chunking-strategy.md)。

### ③ 埋め込み / 格納（インターフェース抽象化）
```python
class Embedder(ABC):
    def embed(self, texts: list[str]) -> list[list[float]]: ...   # 1024次元

class VectorStore(ABC):
    def upsert(self, chunks: list[dict], vectors) -> None: ...
    def search(self, query_vector, top_k: int) -> list[dict]: ...
```
| IF | 開発 | 本番 |
|---|---|---|
| Embedder | OllamaEmbedder | BedrockEmbedder |
| VectorStore | PgVectorStore（Docker） | PgVectorStore（Aurora・同一実装） |

### 既知の限界（MVP）

- **多段組みの読み順**: ① は行を `(page, y0, x0)` で整列するため、2段組みは左右段が交互になりうる。
  対象は単段の素直な PDF を想定。段組み対応は将来課題（ブロックの列クラスタリング等）。
- **見出しレベルの不連続**: ② は見出しパスの位置で `chapter`/`section` を決めるため、`#`→`###` のように
  レベルが飛ぶ構成では割り当てがずれうる。① の出力（書名=`#`/章=`##`/節=`###`）では問題ない。
- **page 未取得**: チャンクの `page` は MVP では `null`（列は将来の表示用に保持）。
  ページ番号を持たせるには ① がページ境界を中間データに残す拡張が必要（[ADR 0001](adr/0001-layer-separation.md) の制約参照）。

## 8. ディレクトリ構成

[README.md](../README.md#ディレクトリ構成目標) を参照（`books/{raw,normalized,chunks}` / `workers/{extract,chunk,embed}` / `infra/db` / `docker` / `tests/fixtures` / `docs`）。

## 9. 本番移行フロー（開発 → AWS）

1. ローカルで全フェーズを通し検索精度を確認・チューニング。
2. `chunks/*.jsonl` を正本として確定（①②の再実行は不要）。
3. AWS リソース作成（SQS×3+DLQ×3、S3、Fargate タスク定義、Lambda×2、Aurora pgvector、Secrets Manager）。
4. 設定のみ切替（Embedder→BedrockEmbedder、SQS/S3 エンドポイント→本番、環境変数で制御）。
5. `chunks/*.jsonl` から BedrockEmbedder で再埋め込み → Aurora pgvector へ投入。
6. 本番モデル（Titan）での検索精度を再評価。

## 10. 技術選定の根拠

| コンポーネント | 選定 | 要点 |
|---|---|---|
| ① 抽出 基盤 | ECS Fargate（min=0） | フォント相対判定に書籍全体が必要・1冊1プロセス・実行時間制限なし |
| ②③ 基盤 | Lambda（SQSトリガー） | 数秒〜数十秒・状態不要・アイドル課金なし |
| キュー | SQS | Point-to-Point・保持・DLQ・リトライ |
| 抽出 | PyMuPDF | span 単位でフォントサイズ取得・OCR 不要 |
| 開発埋め込み | Ollama `bge-m3` | API 構造を本番と統一・1024次元（→ [ADR 0005](adr/0005-ollama-dev-embeddings.md)） |
| 本番埋め込み | Titan V2（Bedrock） | 1024/512/256次元・boto3 で invoke_model |
| DB 認証 | Secrets Manager | Lambda が Aurora 接続情報を取得 |
| ベクトル DB | pgvector | 開発(Docker)/本番(Aurora)でエンジン・スキーマ共通 |

## 11. 法務・コンプライアンス

購入済み電子書籍のベクトル化保持は規約・著作権に触れうる。**出典明示**（book_id/title/page をメタデータ保持）・
**抜粋範囲の制限**・**アクセス制御**を設計に織り込む。DRM 付き書籍は購入形態・規約を事前確認。
（リポジトリ運用上の扱いは [README のデータ取り扱いルール](../README.md#セキュリティ--データ取り扱いルール厳守) を参照）

## 12. 未決事項 / Open Questions

- 回答生成パイプライン（検索→コンテキスト付与→回答）の設計（別 Doc）。
- ハイブリッド検索（pgvector + 日本語全文検索 pg_bigm 等）の要否。
- Reranker 導入の要否（検索精度チューニング時に判断）。
- 書籍冊数のスケール見込み（Aurora サイズ・SQS スループット試算）。
- 最大タスク数 N（同時処理冊数の上限）。
- 処理状況のモニタリング（DLQ アラート・キュー深度ダッシュボード）。
