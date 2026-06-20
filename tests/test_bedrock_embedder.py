"""BedrockEmbedder のユニットテスト（boto3 をモック）。"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from workers.embed.bedrock_embedder import BedrockEmbedder


def _fake_client(vec: list[float]) -> MagicMock:
    """invoke_model が vec を返す偽 boto3 クライアント。BytesIO を毎回新規生成する。"""
    client = MagicMock()
    client.invoke_model.side_effect = lambda **kw: {
        "body": io.BytesIO(json.dumps({"embedding": vec}).encode())
    }
    return client


def test_embed_returns_vector():
    vec = [0.1, 0.2, 0.3]
    with patch("boto3.client", return_value=_fake_client(vec)):
        emb = BedrockEmbedder("titan-v2", dim=3)
    assert emb.embed(["hello"]) == [vec]


def test_embed_sends_correct_payload():
    vec = [0.1, 0.2, 0.3]
    captured: dict = {}

    def _invoke(**kwargs):
        captured.update(kwargs)
        return {"body": io.BytesIO(json.dumps({"embedding": vec}).encode())}

    client = MagicMock()
    client.invoke_model.side_effect = _invoke

    with patch("boto3.client", return_value=client):
        emb = BedrockEmbedder("my-model", dim=3, region="us-east-1")
    emb.embed(["テスト"])

    assert captured["modelId"] == "my-model"
    body = json.loads(captured["body"])
    assert body["inputText"] == "テスト"
    assert body["dimensions"] == 3
    assert body["normalize"] is True


def test_embed_multiple_texts_one_invoke_per_text():
    vec = [0.0, 1.0]
    with patch("boto3.client", return_value=_fake_client(vec)):
        emb = BedrockEmbedder("m", dim=2)
    result = emb.embed(["a", "b", "c"])
    assert len(result) == 3
    assert emb._client.invoke_model.call_count == 3


def test_embed_dim_mismatch_raises():
    wrong_vec = [0.1, 0.2]  # dim=3 を期待するが 2 次元
    with patch("boto3.client", return_value=_fake_client(wrong_vec)):
        emb = BedrockEmbedder("m", dim=3)
    with pytest.raises(ValueError, match="次元不一致"):
        emb.embed(["hello"])
