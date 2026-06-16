"""③ 埋め込み / 格納層のインターフェース契約（CLAUDE.md）。

開発/本番で実装を差し替えるための抽象。次元は開発/本番とも 1024。
  - 開発: OllamaEmbedder / PgVectorStore
  - 本番: BedrockEmbedder / PgVectorStore(Aurora・同一実装)
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """テキスト列を 1024 次元ベクトル列に変換する。"""
        ...


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        """チャンク（メタデータ）とベクトルを格納する（再投入は冪等）。"""
        ...

    @abstractmethod
    def search(self, query_vector: list[float], top_k: int) -> list[dict]:
        """クエリベクトルに近いチャンクを上位 top_k 件返す。"""
        ...
