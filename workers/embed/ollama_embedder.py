"""開発用 Embedder: Ollama の埋め込み API を叩く。

本番 BedrockEmbedder と「埋め込み専用サーバーの API を呼ぶ」構造を揃える。
"""

from __future__ import annotations

import httpx

from .base import Embedder


class OllamaEmbedder(Embedder):
    def __init__(
        self,
        host: str,
        model: str,
        dim: int,
        batch_size: int = 32,
        timeout: float = 120.0,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = httpx.post(
                f"{self.host}/api/embed",
                json={"model": self.model, "input": batch},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            embeddings = resp.json().get("embeddings")
            got = len(embeddings) if embeddings else 0
            if got != len(batch):
                raise RuntimeError(f"Ollama の応答が不正です: 入力 {len(batch)} 件に対し {got} 件")
            for vec in embeddings:
                if len(vec) != self.dim:
                    raise ValueError(f"次元不一致: 期待 {self.dim}, 実際 {len(vec)}")
            vectors.extend(embeddings)
        return vectors
