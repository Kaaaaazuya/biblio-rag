from __future__ import annotations

from abc import ABC, abstractmethod


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        """chunks を query との関連度で再スコアリングし、上位 top_k 件を返す。"""
