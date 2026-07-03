"""WebUI チャット: Markdown レンダリングライブラリが実際に動作することの検証（Issue #36）。

ローカル同梱した marked.min.js がプレースホルダではなく実物であり、
ブラウザ上で実際に Markdown を HTML に変換できることを検証する。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = pytest.mark.webui


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    """webui.server を uvicorn サブプロセスで起動する（静的ファイル配信のみ。MinIO/DB は不要）。"""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "webui.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/chat.html", timeout=1.0).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            _, stderr = proc.communicate(timeout=10)
            pytest.fail(f"uvicorn が起動しませんでした。\nSTDERR:\n{stderr}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def test_marked_renders_markdown_in_browser(page, server):
    """chat.html 上で marked.parse がヘッダー・太字・コードを実際に HTML へ変換することを確認。"""
    page.goto(f"{server}/chat.html")

    rendered = page.evaluate("() => marked.parse('# 見出し\\n\\n**太字** と `コード`')")

    assert "<h1" in rendered, f"見出しがレンダリングされていない: {rendered}"
    assert "<strong>太字</strong>" in rendered, f"太字がレンダリングされていない: {rendered}"
    assert "<code>コード</code>" in rendered, f"コードがレンダリングされていない: {rendered}"


def test_restored_history_renders_markdown(page, server):
    """ページリロード後の履歴復元でも Markdown レンダリングが行われることを確認する（Issue #37）。

    送信時と同じレンダリング処理（marked.parse + DOMPurify.sanitize）を経ているかを検証する。
    """
    page.goto(f"{server}/chat.html")

    page.evaluate(
        """() => {
            localStorage.setItem("biblio-rag:chat-v1", JSON.stringify({
                history: [
                    { role: "user", content: "質問" },
                    { role: "assistant", content: "**太字** と `コード`" },
                ],
                displayed: [
                    { role: "user", text: "質問" },
                    { role: "assistant", text: "**太字** と `コード`" },
                ],
                lang: "ja",
                persona: "",
                book: "",
            }));
        }"""
    )
    page.reload()

    assistant_bubble = page.locator(".message.assistant .bubble").first
    assert "rendered" in (assistant_bubble.get_attribute("class") or ""), (
        "復元されたassistantメッセージがMarkdownレンダリングされていない"
    )
    inner_html = assistant_bubble.inner_html()
    assert "<strong>太字</strong>" in inner_html, (
        f"太字が復元時にレンダリングされていない: {inner_html}"
    )
    assert "<code>コード</code>" in inner_html, (
        f"コードが復元時にレンダリングされていない: {inner_html}"
    )

    # user メッセージはプレーンテキストのまま（従来通り）
    user_bubble = page.locator(".message.user .bubble").first
    assert user_bubble.inner_text() == "質問"
