# 0005. 開発埋め込みに Ollama を採用

- 状態: Accepted
- 日付: 2026-06-16
- 関連: [design.md](../design.md) §6, §10、[ADR 0006](0006-local-to-aws.md)

## コンテキスト

開発はローカル完結（無料）にしたい。本番は Bedrock Titan V2（API 呼び出し）。開発と本番で
コード構造を揃え、移行時の差分を小さくしたい。

## 決定

開発の埋め込みに **Ollama `bge-m3`（1024次元・`localhost:11434/api/embed`）** を採用する。
`Embedder` インターフェースの実装として `OllamaEmbedder` を用意する。

## 理由

- Ollama は localhost で API を提供するため、本番 Bedrock の「埋め込み専用サーバーの API を叩く」
  構造に揃う（`Embedder` 実装を差し替えるだけ）。
- `bge-m3` は多言語対応（100言語超）で日本語も実用範囲。**1024次元**で Titan V2 とスキーマを共通化できる。
- 精度より開発しやすさを優先した選択。精度を上げたい場合は `qwen3-embedding` 等に差し替え可能。

## 結果

- 良い点: 開発¥0・本番と同じ呼び出し構造・スキーマ共通化（`VECTOR(1024)`）。
- 悪い点: 意味空間は Titan と別物なので、本番移行時は再埋め込みが必要（[ADR 0006](0006-local-to-aws.md)）。
- 注記: macOS の Docker 内 Ollama は Metal GPU を使えず CPU 動作（速度が要るなら native Ollama に切替可）。
