# ADR 0012 — 最小 RAG チャット UI の追加

- ステータス: 採用
- 作成日: 2026-06-18

## 背景

MVP（T1〜T5）で取り込みパイプラインが完成し、pgvector への検索が `task search` で動くようになった。
検索精度のチューニングと動作確認のため、ブラウザから直接 RAG を試せる UI が欲しかった。

## 決定

既存の Starlette WebUI（`webui/server.py`）に `/api/chat` エンドポイントを追加し、
`/chat.html` + `/chat.js` からアクセスできる最小チャット UI を実装する。

## 設計要点

| 項目 | 選択 | 理由 |
|---|---|---|
| 生成 LLM | Ollama（`qwen2.5:7b` デフォルト） | 開発環境ですでに Ollama を使用中。`CHAT_MODEL` 環境変数で差し替え可 |
| ストリーミング | SSE over POST（fetch + ReadableStream） | EventSource は GET のみのため POST-based SSE を fetch で実装 |
| SSE イベント型 | `sources` → `token` ×N → `done` / `error` | ソースチップを生成完了後に表示することでチラツキを防ぐ |
| 非同期ブリッジ | `asyncio.get_event_loop().run_in_executor` | `_retrieve`（同期）を async ハンドラから呼ぶため |
| 会話履歴 | localStorage（`biblio-rag:chat-v1`） | MVP 用途では DB 永続化は不要 |
| Markdown | marked.js（CDN） | ストリーム中はプレーンテキスト、完了後に `marked.parse()` で変換 |
| ペルソナ / 言語 | system prompt に prefix 挿入 | 実装コストが低く効果的 |

## トレードオフ

- **速度**: Docker 内 Ollama は Metal GPU を使えず CPU 動作。native Ollama 使用を推奨。
- **履歴のセキュリティ**: localStorage は端末依存・暗号化なし。書籍内容の一部がブラウザに残る。
  本番ユースケースではサーバー側の会話管理が必要。
- **本番 Bedrock 対応**: 現状は Ollama 専用。本番移行時は `CHAT_MODEL` 変数と httpx の呼び出し先の
  変更が必要（Embedder 抽象化のように LLM 層も抽象化するか、その時点で判断）。

## 却下した代替案

- **FastAPI + LangChain**: 既存スタック（Starlette）で十分なため不採用。過剰。
- **OpenAI Responses API への移行**: 開発環境はローカル完結が方針のため不採用。
