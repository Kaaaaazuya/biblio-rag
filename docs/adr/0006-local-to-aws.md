# 0006. 開発ローカル完結 → 本番 AWS 移行

- 状態: Accepted（[ADR 0015](0015-zero-cost-deployment.md) によりベクトル DB のみ Aurora → Neon に変更。埋め込み(Bedrock)・本番構成の他要素は本 ADR のまま）
- 日付: 2026-06-16
- 関連: [design.md](../design.md) §6, §9、[ADR 0001](0001-layer-separation.md)、[ADR 0005](0005-ollama-dev-embeddings.md)、[ADR 0015](0015-zero-cost-deployment.md)

## コンテキスト

開発は無料・高速に回したい。本番は AWS マネージドで運用したい。両環境で実装を二重持ちせず、
移行コストを最小化したい。

## 決定

開発はローカル完結（Ollama + Docker pgvector）、本番は AWS（Bedrock + Aurora pgvector）とし、
**`chunks/*.jsonl` を正本に、本番では再埋め込みして移行**する。次元は開発/本番とも **1024 に統一**。

## 理由

- 次元を 1024 で揃えるとスキーマ（`VECTOR(1024)`）・インデックス定義を変えずモデルだけ差し替えられる。
- ①② の処理コードは環境非依存で共通化でき、差異は「埋め込みモデル」と「接続先」の2点に閉じる。

## 結果

- 良い点: 移行が設定切替＋再埋め込みで済む。`PgVectorStore` は Docker/Aurora で同一実装。
- 悪い点: **次元が同じでも意味空間はモデルごとに別物**。ローカルのベクトルは本番でそのまま使えず、
  `chunks/*.jsonl` からの再埋め込みが必須。移行後に本番モデル（Titan）で検索精度を再評価する。
- 本番認証: DB 認証は Secrets Manager 経由。AWS 実キーは開発では不要（ダミーで足りる）。
