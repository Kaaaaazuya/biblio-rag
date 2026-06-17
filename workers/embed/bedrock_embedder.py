"""本番用 Embedder: Amazon Bedrock Titan Embeddings V2 を呼び出す。

EMBED_BACKEND=bedrock のとき pipeline.py から使われる。
Bedrock はバッチ API 非対応（同期呼び出し）のため 1件ずつ invoke_model する。
"""

from __future__ import annotations

import json

import boto3

from .base import Embedder


class BedrockEmbedder(Embedder):
    def __init__(self, model_id: str, dim: int, region: str = "ap-northeast-1"):
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            body = json.dumps({"inputText": text, "dimensions": self.dim, "normalize": True})
            resp = self._client.invoke_model(
                modelId=self.model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            vec = json.loads(resp["body"].read())["embedding"]
            if len(vec) != self.dim:
                raise ValueError(f"次元不一致: 期待 {self.dim}, 実際 {len(vec)}")
            vectors.append(vec)
        return vectors
