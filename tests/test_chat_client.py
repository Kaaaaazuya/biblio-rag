"""チャット生成層のテスト。"""

from __future__ import annotations

from workers.chat.base import ChatClient
from workers.chat.ollama_chat import OllamaChatClient


def test_ollama_chat_client_interface():
    """OllamaChatClient が ChatClient インターフェースを実装することを確認。"""
    client = OllamaChatClient("http://localhost:11434", "test-model")
    assert isinstance(client, ChatClient)


def test_ollama_chat_client_initialization():
    """OllamaChatClient が正しく初期化されることを確認。"""
    client = OllamaChatClient("http://localhost:11434", "my-model", timeout=60.0)
    assert client.host == "http://localhost:11434"
    assert client.model == "my-model"
    assert client.timeout == 60.0


def test_ollama_chat_client_strips_trailing_slash():
    """OllamaChatClient が host の末尾スラッシュを削除することを確認。"""
    client = OllamaChatClient("http://localhost:11434/", "test-model")
    assert client.host == "http://localhost:11434"
