# 0008. raw PDF をオブジェクトストレージに置く（開発 MinIO / 本番 S3）

- 状態: Accepted
- 日付: 2026-06-16
- 関連: [design.md](../design.md) §4, §6、[ADR 0006](0006-local-to-aws.md)

## コンテキスト

将来 **WebUI から書籍 PDF をアップロード**できるようにしたい。アップロード先がローカル FS や
git リポジトリだと、Web 配信・本番運用に繋がらない。Design Doc でも「S3 がデータのバケツ」と
位置づけており、保存はオブジェクトストレージが自然。

当初 Design Doc はローカルの S3 代替に **LocalStack** を想定していたが、S3 互換・軽量で
**Web 管理コンソールを持つ MinIO** の方がアップロード運用の確認に向く。

## 決定

- raw PDF の保存先を **オブジェクトストレージ**にする。開発 = **MinIO**、本番 = **AWS S3**。
- boto3 を用い、接続先（`S3_ENDPOINT_URL`）だけで開発/本番を切り替える（MinIO 指定 / 空=AWS）。
- アクセスは `workers/storage.py` の `ObjectStore` を介する。`workers.extract` は既定で
  S3 の `raw/` から PDF を取得する（ローカルパス引数も併用可）。投入補助に `workers.upload`。
- **まずは raw PDF のみ**を対象とし、`normalized/*.md`・`chunks/*.jsonl` は当面ローカル FS に置く。

## 理由

- MinIO は S3 完全互換で軽量、コンソール（`:9001`）でアップロード結果を目視できる → WebUI 開発と相性が良い。
- raw から段階導入することで変更範囲を抑えつつ、アップロード起点（最重要）を先に S3 化できる。
- LocalStack は SQS/Lambda を扱う 2nd ステージ用途として温存できる（本 ADR と競合しない）。

## 結果

- 良い点: アップロード起点が本番と同じ S3 API になり、WebUI 化の布石になる。dev/本番でコード共通。
- 悪い点: 開発時も MinIO 起動が前提になる。保存先が **S3(raw) と FS(normalized/chunks) で混在**する。
- 継ぎ目: 将来 normalized/chunks も S3 へ寄せる際は `ObjectStore` を拡張して各ワーカーを差し替える
  （`Embedder`/`VectorStore`/`Chunker` と同じ抽象化の流れ）。
- 認証: 開発は MinIO のダミー資格情報（`minioadmin`）を `.env` に置く。本番は実キーを置かず IAM ロール。
