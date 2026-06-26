from __future__ import annotations

from abc import ABC, abstractmethod


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """候補を再スコアリングして上位 top_k 件を返す。結果に rerank_score を付与する。"""
