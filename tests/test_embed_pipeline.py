"""embed/pipeline.py のユニットテスト（外部接続なし）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import workers.config as config
from workers.embed.base import Embedder, VectorStore
from workers.embed.pipeline import active_embed_model, embed_and_store, make_embedder


class _FakeEmbedder(Embedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class _FakeStore(VectorStore):
    def __init__(self) -> None:
        self.upserted: list[tuple] = []

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        self.upserted.append((chunks, vectors))

    def search(self, query_vector: list[float], top_k: int) -> list[dict]:
        return []


def test_make_embedder_default_returns_ollama(monkeypatch):
    monkeypatch.setattr(config, "EMBED_BACKEND", "ollama")
    from workers.embed.ollama_embedder import OllamaEmbedder

    assert isinstance(make_embedder(), OllamaEmbedder)


def test_make_embedder_bedrock_returns_bedrock(monkeypatch):
    monkeypatch.setattr(config, "EMBED_BACKEND", "bedrock")
    with patch("boto3.client", return_value=MagicMock()):
        emb = make_embedder()
    from workers.embed.bedrock_embedder import BedrockEmbedder

    assert isinstance(emb, BedrockEmbedder)


def test_active_embed_model_ollama(monkeypatch):
    monkeypatch.setattr(config, "EMBED_BACKEND", "ollama")
    monkeypatch.setattr(config, "EMBED_MODEL", "bge-m3-custom")
    assert active_embed_model() == "bge-m3-custom"


def test_active_embed_model_bedrock(monkeypatch):
    monkeypatch.setattr(config, "EMBED_BACKEND", "bedrock")
    monkeypatch.setattr(config, "BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
    assert active_embed_model() == "amazon.titan-embed-text-v2:0"


def test_embed_and_store_attaches_embed_model():
    recs = [{"book_id": "b", "chunk_index": 0, "text": "t"}]
    store = _FakeStore()
    embed_and_store(recs, _FakeEmbedder(), store, embed_model="bge-m3")
    stored = store.upserted[0][0]
    assert stored[0]["embed_model"] == "bge-m3"


def test_embed_and_store_no_model_omits_field():
    recs = [{"book_id": "b", "chunk_index": 0, "text": "t"}]
    store = _FakeStore()
    embed_and_store(recs, _FakeEmbedder(), store)
    stored = store.upserted[0][0]
    assert "embed_model" not in stored[0]
