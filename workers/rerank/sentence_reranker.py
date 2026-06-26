from __future__ import annotations

from sentence_transformers.cross_encoder import CrossEncoder

from .base import Reranker


class SentenceReranker(Reranker):
    def __init__(self, model_name: str) -> None:
        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates:
            return []

        pairs = [(query, c["text"]) for c in candidates]
        scores = self._model.predict(pairs)

        scored = [{**c, "rerank_score": float(s)} for c, s in zip(candidates, scores, strict=True)]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]
