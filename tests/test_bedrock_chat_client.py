"""BedrockChatClient のユニットテスト（boto3 をモック）。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from workers.chat.base import ChatClient
from workers.chat.bedrock_chat import BedrockChatClient


def _fake_client(events: list[dict]) -> MagicMock:
    """converse_stream が events を順に返す偽 boto3 クライアント。"""
    client = MagicMock()
    client.converse_stream.return_value = {"stream": iter(events)}
    return client


async def _collect(client: BedrockChatClient, messages: list[dict]) -> list[str]:
    return [token async for token in client.stream_chat(messages)]


def test_bedrock_chat_client_interface():
    with patch("boto3.client", return_value=_fake_client([])):
        client = BedrockChatClient("test-model")
    assert isinstance(client, ChatClient)


def test_bedrock_chat_client_initialization():
    with patch("boto3.client", return_value=_fake_client([])):
        client = BedrockChatClient("my-model", region="us-east-1")
    assert client.model_id == "my-model"


def test_bedrock_chat_client_passes_timeout_to_config():
    captured: dict = {}

    def _client_factory(*args, **kwargs):
        captured.update(kwargs)
        return _fake_client([])

    with patch("boto3.client", side_effect=_client_factory):
        BedrockChatClient("my-model", timeout=15.0)

    assert captured["config"].read_timeout == 15.0


def test_bedrock_chat_client_no_config_when_timeout_not_given():
    captured: dict = {}

    def _client_factory(*args, **kwargs):
        captured.update(kwargs)
        return _fake_client([])

    with patch("boto3.client", side_effect=_client_factory):
        BedrockChatClient("my-model")

    assert captured["config"] is None


def test_stream_chat_yields_text_deltas():
    events = [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "こん"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "にちは"}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    with patch("boto3.client", return_value=_fake_client(events)):
        client = BedrockChatClient("my-model")

    tokens = asyncio.run(_collect(client, [{"role": "user", "content": "hi"}]))
    assert tokens == ["こん", "にちは"]


def test_stream_chat_sends_system_separately_from_messages():
    captured: dict = {}

    def _converse_stream(**kwargs):
        captured.update(kwargs)
        return {"stream": iter([])}

    client_mock = MagicMock()
    client_mock.converse_stream.side_effect = _converse_stream

    with patch("boto3.client", return_value=client_mock):
        client = BedrockChatClient("my-model")

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hi"},
    ]
    asyncio.run(_collect(client, messages))

    assert captured["modelId"] == "my-model"
    assert captured["system"] == [{"text": "system prompt"}]
    assert captured["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]


def test_stream_chat_ignores_non_text_deltas():
    events = [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}},
    ]
    with patch("boto3.client", return_value=_fake_client(events)):
        client = BedrockChatClient("my-model")

    tokens = asyncio.run(_collect(client, [{"role": "user", "content": "hi"}]))
    assert tokens == ["ok"]


def _raising_stream(events: list[dict], error: Exception):
    """boto3 の EventStream を模したイテレータ。events を返した後 error を送出する。

    実際の boto3 は throttlingException 等のサービス例外をイベントとして返すのではなく、
    ストリームの反復中に EventStreamError（Exception のサブクラス）を送出する。
    """
    yield from events
    raise error


def test_stream_chat_raises_runtime_error_on_throttling():
    events = [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "partial"}}}]
    error = Exception("Too many requests")
    client_mock = MagicMock()
    client_mock.converse_stream.return_value = {"stream": _raising_stream(events, error)}

    with patch("boto3.client", return_value=client_mock):
        client = BedrockChatClient("my-model")

    async def _run():
        return [token async for token in client.stream_chat([{"role": "user", "content": "hi"}])]

    with pytest.raises(RuntimeError, match="Too many requests"):
        asyncio.run(_run())


def test_stream_chat_uses_model_override():
    captured: dict = {}

    def _converse_stream(**kwargs):
        captured.update(kwargs)
        return {"stream": iter([])}

    client_mock = MagicMock()
    client_mock.converse_stream.side_effect = _converse_stream

    with patch("boto3.client", return_value=client_mock):
        client = BedrockChatClient("default-model")

    async def _run():
        return [
            token
            async for token in client.stream_chat(
                [{"role": "user", "content": "hi"}], model="override-model"
            )
        ]

    asyncio.run(_run())
    assert captured["modelId"] == "override-model"


class _CloseableStream:
    """close() 呼び出しを検知できる偽ストリーム。閉じられると反復を打ち切る。"""

    def __init__(self, events: list[dict]):
        self._it = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        return next(self._it)

    def close(self) -> None:
        self.closed = True


def test_stream_chat_closes_stream_on_early_consumer_exit():
    events = [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "a"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "b"}}},
    ]
    fake_stream = _CloseableStream(events)
    client_mock = MagicMock()
    client_mock.converse_stream.return_value = {"stream": fake_stream}

    with patch("boto3.client", return_value=client_mock):
        client = BedrockChatClient("my-model")

    async def _run():
        gen = client.stream_chat([{"role": "user", "content": "hi"}])
        token = await gen.__anext__()
        assert token == "a"
        await gen.aclose()

    asyncio.run(_run())
    assert fake_stream.closed is True
