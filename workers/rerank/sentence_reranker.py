"""Reranker 実装: sentence-transformers CrossEncoder。

CrossEncoder インスタンスはクラスレベルのキャッシュ（_cache）で保持し、
同じモデル名であればプロセス内で1回だけロードする。
_lock で初期化の競合を防ぐ（run_in_executor 経由のマルチスレッド呼び出し対応）。
"""

from __future__ import annotations

import threading
from typing import Any

from .base import Reranker

# sentence-transformers の CrossEncoder はこの境界だけ Any として扱う
# （型スタブの有無・正確性がバージョン依存のため。実行時は CrossEncoder インスタンス）。


class SentenceReranker(Reranker):
    _cache: dict[str, Any] = {}
    _lock = threading.Lock()

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def _load(self) -> Any:
        with SentenceReranker._lock:
            if self._model_name not in SentenceReranker._cache:
                from sentence_transformers import CrossEncoder

                SentenceReranker._cache[self._model_name] = CrossEncoder(self._model_name)
            return SentenceReranker._cache[self._model_name]

    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        model = self._load()
        pairs = [(query, c["text"]) for c in chunks]
        scores = model.predict(pairs)
        ranked = sorted(zip(scores, chunks, strict=False), key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked[:top_k]]
