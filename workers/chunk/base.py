"""② チャンク層のインターフェース契約（ADR 0007）。

分割戦略を将来差し替えられるよう抽象化する（Embedder / VectorStore と同じ思想）。
既定実装は HeuristicChunker。将来 埋め込みベース / LLM 版を差し込める。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Chunker(ABC):
    @abstractmethod
    def chunk(self, md: str, meta: dict) -> list[dict]:
        """構造つき Markdown をチャンク辞書のリストに変換する。"""
        ...
