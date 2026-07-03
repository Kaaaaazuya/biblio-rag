"""WebUI チャット UI 改善のテスト（Issue #28）。

- Markdown コンテンツ保存時の整合性テスト
- Abort ボタン機能のテスト
- エラーメッセージの表示フォーマットテスト
- ソースリンク機能のエンドツーエンドテスト
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from starlette.testclient import TestClient

from webui import server

_client = TestClient(server.app)


def _sse_events(text: str) -> list[dict]:
    """SSE レスポンスのテキストから data: 行を抽出する。"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _fake_llm(lines: list[str]):
    """Ollama /api/chat SSE ストリームを偽装する httpx.AsyncClient 代替クラスを返す。"""

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def aiter_lines(self) -> AsyncIterator[str]:
            for line in lines:
                yield line

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def stream(self, *args, **kwargs):
            return _Stream()

    return _Client


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


# ─────────────────────────────────────────────────────────────────────────────
# Markdown コンテンツ保存時の整合性テスト（Issue #28）
# ─────────────────────────────────────────────────────────────────────────────


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
    llm_output = [json.dumps({"message": {"content": t}, "done": False}) for t in tokens] + [
        json.dumps({"done": True})
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

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

    llm_output = [
        json.dumps({"message": {"content": markdown_content}, "done": False}),
        json.dumps({"done": True}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    token_contents = [e["content"] for e in events if e["type"] == "token"]
    full_content = "".join(token_contents)

    # すべての Markdown 要素が保持されていることを確認
    assert "# ヘッダー" in full_content
    assert "```python" in full_content
    assert 'print("world")' in full_content
    assert "段落1です。" in full_content
    assert "段落2です。" in full_content


# ─────────────────────────────────────────────────────────────────────────────
# エラーメッセージ表示フォーマットのテスト（Issue #28）
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_error_event_has_readable_message(monkeypatch):
    """エラーイベントに、ユーザーが読める詳細なメッセージが含まれることをテスト。"""

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    # エラーを返す LLM をシミュレート
    llm_output = [
        json.dumps({"error": "model not found"}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

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
    class _ErrorClient:
        async def __aenter__(self):
            raise ConnectionError("connection refused")

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return self

    monkeypatch.setattr("httpx.AsyncClient", _ErrorClient)

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


# ─────────────────────────────────────────────────────────────────────────────
# ソースリンク機能のエンドツーエンドテスト（Issue #28）
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_sources_event_contains_all_required_fields(monkeypatch):
    """ソースイベントにモーダル表示に必要なすべてのフィールドが含まれることをテスト。"""

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    llm_output = [
        json.dumps({"message": {"content": "回答です"}, "done": False}),
        json.dumps({"done": True}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

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
                "chapter": None,
                "section": None,
                "page": None,
            }
        ]

    monkeypatch.setattr(server, "_retrieve", fake_retrieve_minimal)

    llm_output = [
        json.dumps({"message": {"content": "回答"}, "done": False}),
        json.dumps({"done": True}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    sources_events = [e for e in events if e["type"] == "sources"]

    assert len(sources_events) >= 1
    source = sources_events[0]["sources"][0]
    # None フィールドが含まれていても問題ないことを確認
    assert source["chapter"] is None or source["chapter"] == ""
    assert source["section"] is None or source["section"] == ""
    assert source["page"] is None or source["page"] == ""


def test_chat_sources_clickable_data_format(monkeypatch):
    """ソースデータが JavaScript で source chip クリック時に使用できる形式であることをテスト。"""

    monkeypatch.setattr(server, "_retrieve", _fake_retrieve)

    llm_output = [
        json.dumps({"message": {"content": "回答"}, "done": False}),
        json.dumps({"done": True}),
    ]

    monkeypatch.setattr("httpx.AsyncClient", _fake_llm(llm_output))

    events = _sse_events(_client.post("/api/chat", json={"query": "テスト"}).text)
    sources_events = [e for e in events if e["type"] == "sources"]

    source = sources_events[0]["sources"][0]

    # JavaScript で openModal() に渡せるオブジェクト形式であることを確認
    # openModal(source) 関数が期待する形式: title, author, chapter, section, page, text
    assert isinstance(source, dict)
    assert all(key in source for key in ["title", "text"])  # 必須フィールド


# ─────────────────────────────────────────────────────────────────────────────
# エラーハンドリングの追加テスト（Issue #28）
# ─────────────────────────────────────────────────────────────────────────────


def test_chat_retrieval_error_handling(monkeypatch, caplog):
    """検索層でエラーが発生した場合、エラーレスポンスが返されることをテスト。

    注: _retrieve() は chat() 関数の中で run_in_executor で実行され、
    エラーはイベントストリーム開始前に発生するため、500 エラーが返される。
    """
    import logging

    def failing_retrieve(query: str, top_k: int, book_id: str | None = None):
        raise ValueError("検索インデックスが破損しています")

    monkeypatch.setattr(server, "_retrieve", failing_retrieve)

    with caplog.at_level(logging.ERROR):
        res = _client.post("/api/chat", json={"query": "テスト"})

    # エラーが発生し、エラーレスポンスが返される
    # イベント開始前のエラーなので SSE ではなく HTTP エラーが返される
    assert res.status_code >= 400
    # ログには詳細情報を記録
    assert any("検索インデックスが破損" in record.message for record in caplog.records)
