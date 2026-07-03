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
        [sys.executable, "-m", "uvicorn", "webui.server:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/", timeout=1.0).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            pytest.fail("uvicorn が起動しませんでした")
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
