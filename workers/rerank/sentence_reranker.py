"""Reranker 実装: sentence-transformers CrossEncoder。

初回呼び出し時にモデルを遅延ロードする（~/.cache/huggingface/ に自動キャッシュ）。
"""

from __future__ import annotations

from .base import Reranker


class SentenceReranker(Reranker):
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None  # 遅延ロード

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)

    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        if not chunks:
            return chunks
        self._load()
        pairs = [(query, c["text"]) for c in chunks]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(scores, chunks, strict=False), key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked[:top_k]]
