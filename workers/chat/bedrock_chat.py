"""本番用 ChatClient: Amazon Bedrock Converse Stream API を呼び出す。

CHAT_BACKEND=bedrock のとき webui/server.py の _make_chat_client から使われる。
Converse Stream API はモデル横断で共通のメッセージ形式を提供するため、
Anthropic Claude 以外の Bedrock モデルにも切替可能。

レート制限・タイムアウト時の挙動: boto3 の EventStream はストリーミング中に
throttlingException 等のサービス例外を受け取ると、イベントとして返すのではなく
botocore.eventstream.EventStreamError を送出する。このため個別のイベント種別を
判定する必要はなく、下の except Exception がそのまま拾って RuntimeError に変換する
（フォールバックはせず、呼び出し元の webui/server.py がエラーイベントとして
ユーザーに通知する既存の方式に委ねる）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import boto3

from .base import ChatClient


def _to_converse_format(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """webui の role/content メッセージ列を Converse API の (system, messages) に変換する。"""
    system: list[dict] = []
    converse_messages: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            system.append({"text": m["content"]})
        else:
            converse_messages.append({"role": m["role"], "content": [{"text": m["content"]}]})
    return system, converse_messages


class BedrockChatClient(ChatClient):
    def __init__(self, model_id: str, region: str = "ap-northeast-1"):
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

    async def stream_chat(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Bedrock Converse Stream API にストリーミングで問い合わせ、トークンを yield する。"""
        use_model = model or self.model_id
        system, converse_messages = _to_converse_format(messages)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        def _run() -> None:
            try:
                kwargs: dict = {"modelId": use_model, "messages": converse_messages}
                if system:
                    kwargs["system"] = system
                resp = self._client.converse_stream(**kwargs)
                for event in resp["stream"]:
                    delta = event.get("contentBlockDelta", {}).get("delta", {})
                    if text := delta.get("text"):
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        future = loop.run_in_executor(None, _run)
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise RuntimeError(str(item)) from item
                yield item
        finally:
            future.cancel()
