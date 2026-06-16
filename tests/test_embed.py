"""③ 埋め込み/格納層のユニットテスト（ライブサービス不要・フェイクで配線を検証）。

実際の pgvector / Ollama を使った疎通は統合確認（README の手順）で行う。
"""

import pytest

from workers.embed import embed_and_store
from workers.embed.base import Embedder, VectorStore
from workers.embed.ollama_embedder import OllamaEmbedder
from workers.embed.pgvector_store import _vec_literal


class FakeEmbedder(Embedder):
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts)]


class FakeStore(VectorStore):
    def __init__(self):
        self.upserts: list[tuple[list[dict], list[list[float]]]] = []

    def upsert(self, chunks, vectors):
        self.upserts.append((chunks, vectors))

    def search(self, query_vector, top_k):
        return []


def _records(n):
    return [{"book_id": "b", "chunk_index": i, "text": f"t{i}"} for i in range(n)]


def test_embed_and_store_wires_texts_to_vectors():
    emb, store = FakeEmbedder(), FakeStore()
    recs = _records(3)
    n = embed_and_store(recs, emb, store)
    assert n == 3
    assert emb.calls == [["t0", "t1", "t2"]]  # text 列だけを渡す
    chunks, vectors = store.upserts[0]
    assert chunks is recs and len(vectors) == 3  # チャンクとベクトルが対応


def test_embed_and_store_empty_skips_calls():
    emb, store = FakeEmbedder(), FakeStore()
    assert embed_and_store([], emb, store) == 0
    assert emb.calls == [] and store.upserts == []


def test_vec_literal_format():
    assert _vec_literal([1, 2, 3]) == "[1.0,2.0,3.0]"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_ollama_embedder_batches_and_validates_dim(monkeypatch):
    seen = []

    def fake_post(url, json, timeout):
        seen.append(json["input"])
        return _FakeResp({"embeddings": [[0.0, 0.0] for _ in json["input"]]})

    monkeypatch.setattr("workers.embed.ollama_embedder.httpx.post", fake_post)
    emb = OllamaEmbedder("http://x", "bge-m3", dim=2, batch_size=2)
    out = emb.embed(["a", "b", "c"])
    assert len(out) == 3
    assert seen == [["a", "b"], ["c"]]  # batch_size=2 で分割


def test_ollama_embedder_dim_mismatch_raises(monkeypatch):
    def fake_post(url, json, timeout):
        return _FakeResp({"embeddings": [[0.0, 0.0, 0.0] for _ in json["input"]]})

    monkeypatch.setattr("workers.embed.ollama_embedder.httpx.post", fake_post)
    emb = OllamaEmbedder("http://x", "bge-m3", dim=1024)
    with pytest.raises(ValueError):
        emb.embed(["a"])
