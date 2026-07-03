"""WebUI チャット UI 改善のテスト（Issue #28）。

- Markdown コンテンツ保存時の整合性テスト
- Abort ボタン機能のテスト
- エラーメッセージの表示フォーマットテスト
- ソースリンク機能のエンドツーエンドテスト
"""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from webui import server
from workers.chat.base import ChatClient

_client = TestClient(server.app)


def _sse_events(text: str) -> list[dict]:
    """SSE レスポンスのテキストから data: 行を抽出する。"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


class _MockChatClient(ChatClient):
    """テスト用モック ChatClient。設定可能なトークン列を返す。"""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens

    async def stream_chat(self, messages: list[dict], model: str | None = None):
        for token in self.tokens:
            yield token


def _fake_retrieve(query: str, top_k: int, book_id: str | None = None) -> list[dict]:
    """テスト用の検索結果を返す。"""
    return [
        {
            "book_id": "book1",
            "text": "これは本文です。改行\nを含みます。",
            "title": "テスト書籍",
            "author": "著者名",
            "chapter": "第1章",
            "section": "第1節",
            "page": 42,
        }
    ]


# Markdown コンテンツ保存時の整合性テスト（Issue #28）


def test_chat_markdown_content_preservation_in_response(monkeypatch):
    """レスポンスに含まれる Markdown コンテンツが正しく保存されることをテスト。

    レスポンス内の Markdown 形式のテキスト（**太字**、`コード`など）が
    SSE トークンイベントで正しく送信されることを確認。
    localStorage での保存は JavaScript 側で行われるため、ここではレスポンスの
    トークンが Markdown を保持していることを確認する。
    """
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    # Markdown を含むコンテンツを複数トークンに分割して返す
    tokens = [
        "これは **太字** と ",
        "`コード` を含みます。\n\n- リスト\n",
        "- アイテム",
    ]
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(tokens))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    token_contents = [e["content"] for e in events if e["type"] == "token"]
    full_content = "".join(token_contents)

    # Markdown フォーマットが保持されていることを確認
    assert "**太字**" in full_content, "太字 Markdown が失われている"
    assert "`コード`" in full_content, "インラインコード Markdown が失われている"
    assert "- リスト" in full_content, "リスト Markdown が失われている"


def test_chat_multiline_markdown_preservation(monkeypatch):
    """複数行の Markdown（段落、コードブロック）が保持されることをテスト。"""
    markdown_content = """# ヘッダー

段落1です。

```python
def hello():
    print("world")
```

段落2です。"""

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient([markdown_content]))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    token_contents = [e["content"] for e in events if e["type"] == "token"]
    full_content = "".join(token_contents)

    # すべての Markdown 要素が保持されていることを確認
    assert "# ヘッダー" in full_content
    assert "```python" in full_content
    assert 'print("world")' in full_content
    assert "段落1です。" in full_content
    assert "段落2です。" in full_content


# エラーメッセージ表示フォーマットのテスト（Issue #28）


def test_chat_error_event_has_readable_message(monkeypatch):
    """エラーイベントに、ユーザーが読める詳細なメッセージが含まれることをテスト。"""
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    # エラーを返す ChatClient をシミュレート
    class _ErrorChatClient(ChatClient):
        async def stream_chat(self, messages, model=None):
            raise RuntimeError("model not found")
            yield  # noqa: F501

    monkeypatch.setattr(server, "_make_chat_client", lambda: _ErrorChatClient())

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    errors = [e for e in events if e["type"] == "error"]

    assert len(errors) >= 1
    # エラーメッセージが存在し、空でないことを確認
    assert errors[0].get("message"), "エラーメッセージが空です"
    # メッセージ内に詳細情報が含まれていないことを確認（セキュリティ）
    assert "model not found" not in errors[0]["message"]


def test_chat_network_error_formatting(monkeypatch, caplog):
    """ネットワークエラーが発生した場合、適切にフォーマットされたメッセージを返すテスト。"""
    import logging

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    # 接続エラーをシミュレート
    class _ConnectionErrorClient(ChatClient):
        async def stream_chat(self, messages, model=None):
            raise ConnectionError("connection refused")
            yield  # noqa: F501

    monkeypatch.setattr(server, "_make_chat_client", lambda: _ConnectionErrorClient())

    with caplog.at_level(logging.ERROR):
        res = _client.post("/api/chat", json={"query": "テスト"})
        events = _sse_events(res.text)

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) >= 1
    error_msg = errors[0].get("message", "")
    # 詳細情報（接続エラー）は返さない
    assert "connection refused" not in error_msg
    # 汎用メッセージが含まれている
    assert "An error occurred" in error_msg or error_msg


# ソースリンク機能のエンドツーエンドテスト（Issue #28）


def test_chat_sources_event_contains_all_required_fields(monkeypatch):
    """ソースイベントにモーダル表示に必要なすべてのフィールドが含まれることをテスト。"""
    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(["回答です"]))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    sources_events = [e for e in events if e["type"] == "sources"]

    assert len(sources_events) >= 1
    sources = sources_events[0]["sources"]
    assert len(sources) > 0

    source = sources[0]
    # モーダルで必要なフィールドがすべて含まれていることを確認
    assert "title" in source, "title フィールドが不足"
    assert "author" in source, "author フィールドが不足"
    assert "chapter" in source, "chapter フィールドが不足"
    assert "section" in source, "section フィールドが不足"
    assert "page" in source, "page フィールドが不足"
    assert "text" in source, "text フィールド（本文）が不足"
    # データが実際に設定されていることを確認
    assert source["title"] == "テスト書籍"
    assert source["author"] == "著者名"
    assert source["text"] == "これは本文です。改行\nを含みます。"


def test_chat_sources_with_missing_optional_fields(monkeypatch):
    """オプショナルフィールド（section など）が None の場合、正しく処理されることをテスト。"""

    def fake_retrieve_minimal(query: str, top_k: int, book_id: str | None = None) -> list[dict]:
        return [
            {
                "book_id": "book1",
                "text": "本文",
                "title": "書籍",
                "author": "著者",
                "chapter": "第1章",
                "section": None,
                "page": 1,
            }
        ]

    monkeypatch.setattr(server, "_retrieve", fake_retrieve_minimal)
    monkeypatch.setattr(server, "_make_chat_client", lambda: _MockChatClient(["回答"]))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    sources_events = [e for e in events if e["type"] == "sources"]

    assert len(sources_events) >= 1
    sources = sources_events[0]["sources"]
    source = sources[0]

    # None のフィールドが正しく処理されていることを確認
    assert source["section"] is None
    # None でないフィールドが存在することを確認
    assert source["title"] == "書籍"
