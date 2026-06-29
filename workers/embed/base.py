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
    def search(
        self, query_vector: list[float], top_k: int, book_id: str | None = None
    ) -> list[dict]:
        """クエリベクトルに近いチャンクを上位 top_k 件返す。book_id 指定時は DB でフィルタする。"""
        ...

    def search_keyword(self, query: str, top_k: int, book_id: str | None = None) -> list[dict]:
        """キーワード全文検索で上位 top_k 件返す（HYBRID_ENABLED 時に使用）。
        book_id 指定時はその書籍のみに絞る。
        デフォルトは空リストを返す。pg_bigm 対応実装でオーバーライドする。
        """
        return []
