"""開発用 ChatClient: Ollama のチャット API を叩く。

本番 Bedrock 等と「生成専用サーバーの API を呼ぶ」構造を揃える。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .base import ChatClient

logger = logging.getLogger(__name__)


class OllamaChatClient(ChatClient):
    def __init__(
        self,
        host: str,
        model: str,
        timeout: float = 120.0,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def stream_chat(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Ollama チャット API にストリーミングで問い合わせ、トークンを yield する。"""
        use_model = model or self.model

        async with (
            httpx.AsyncClient(timeout=self.timeout) as client,
            client.stream(
                "POST",
                f"{self.host}/api/chat",
                json={"model": use_model, "messages": messages, "stream": True},
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(data, dict):
                    continue

                if err := data.get("error"):
                    logger.error(f"Ollama error: {err}")
                    raise RuntimeError(f"Ollama error: {err}")

                message = data.get("message")
                if isinstance(message, dict) and (content := message.get("content", "")):
                    yield content

                if data.get("done"):
                    return
