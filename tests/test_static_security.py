"""静的ファイル（HTML・JS）のセキュリティチェックのテスト。

外部 CDN スクリプトのバージョン固定と SRI（Subresource Integrity）の検証。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from webui import server

_client = TestClient(server.app)


def _read_html(path: str) -> str:
    """HTML ファイルを読み込む。"""
    return Path(path).read_text(encoding="utf-8")


def test_chat_html_marked_has_integrity_or_local():
    """chat.html の marked スクリプトが integrity 属性またはローカルパスを持つ。"""
    html = _read_html("webui/static/chat.html")

    # marked スクリプトタグを探す
    marked_script_pattern = (
        r'<script\s+([^>]*)\s*src\s*=\s*["\']([^"\']*marked[^"\']*)["\']([^>]*)>'
    )
    match = re.search(marked_script_pattern, html)

    assert match, "marked スクリプトタグが見つかりません"

    before_src = match.group(1)
    src_url = match.group(2)
    after_src = match.group(3)
    full_tag = before_src + src_url + after_src

    # ローカルパスの場合はチェック完了
    if src_url.startswith("/"):
        assert src_url, "ローカルパスが指定されていることを確認"
        return

    # CDN の場合、バージョンが固定されていることを確認
    assert re.search(r"@\d+\.\d+\.\d+", src_url), (
        f"CDN URL にバージョンが固定されていません: {src_url}"
    )

    # integrity 属性があることを確認
    assert re.search(r'integrity\s*=\s*["\']sha384-[^"\']+["\']', full_tag), (
        f"marked スクリプトに integrity 属性がありません。タグ全体: <script {full_tag}>"
    )

    # crossorigin="anonymous" が設定されていることを確認
    assert re.search(r'crossorigin\s*=\s*["\']anonymous["\']', full_tag), (
        f'marked スクリプトに crossorigin="anonymous" が設定されていません。'
        f"タグ全体: <script {full_tag}>"
    )


def test_chat_html_no_unversioned_cdn_scripts():
    """chat.html に無指定バージョンの CDN スクリプトがないことを確認。"""
    html = _read_html("webui/static/chat.html")

    # CDN スクリプトタグをすべて探す（marked 以外も含む）
    script_pattern = r'<script\s+[^>]*src\s*=\s*["\']([^"\']*)["\']'
    matches = re.findall(script_pattern, html)

    for src_url in matches:
        # ローカルパスはスキップ
        if src_url.startswith("/"):
            continue

        # CDN の場合、バージョンが固定されていることを確認
        if "cdn" in src_url.lower() or "unpkg" in src_url or "jsdelivr" in src_url:
            assert re.search(r"@\d+\.\d+", src_url), (
                f"CDN スクリプトのバージョンが固定されていません: {src_url}"
            )


def test_chat_html_marked_local_path_is_actually_served():
    """chat.html が参照する marked のローカルパスが、実際に配信されることを確認する（Issue #36）。

    StaticFiles は webui/static を "/" にマウントしているため、
    webui/static/lib/marked.min.js の実際の配信パスは /lib/marked.min.js であり、
    /static/lib/marked.min.js ではない。
    """
    html = _read_html("webui/static/chat.html")

    marked_script_pattern = r'<script\s+[^>]*src\s*=\s*["\']([^"\']*marked[^"\']*)["\']'
    match = re.search(marked_script_pattern, html)
    assert match, "marked スクリプトタグが見つかりません"

    src_url = match.group(1)
    if not src_url.startswith("/"):
        pytest.skip("CDN 参照のため配信パス検証は対象外")

    response = _client.get(src_url)
    assert response.status_code == 200, (
        f"chat.html が参照する marked のパス {src_url} が実際には配信されていません"
        f"（status={response.status_code}）"
    )


def test_chat_html_dompurify_local_path_is_actually_served():
    """chat.html が参照する DOMPurify のローカルパスが実際に配信されることを確認する（Issue #37）。

    従来 CDN + SRI で読み込んでいたが、SRI ハッシュが実ファイルと一致しておらず
    ブラウザ側で読み込みがブロックされていた（整合性検証で発覚）。marked と同様に
    ローカル同梱へ切り替えたため、参照パスが実配信パスと一致することを検証する。
    """
    html = _read_html("webui/static/chat.html")

    purify_script_pattern = r'<script\s+[^>]*src\s*=\s*["\']([^"\']*purify[^"\']*)["\']'
    match = re.search(purify_script_pattern, html)
    assert match, "DOMPurify スクリプトタグが見つかりません"

    src_url = match.group(1)
    if not src_url.startswith("/"):
        pytest.skip("CDN 参照のため配信パス検証は対象外")

    response = _client.get(src_url)
    assert response.status_code == 200, (
        f"chat.html が参照する DOMPurify のパス {src_url} が実際には配信されていません"
        f"（status={response.status_code}）"
    )


def test_dompurify_local_file_is_not_a_placeholder_stub():
    """ローカル同梱した DOMPurify が実際に動作するライブラリであることを確認する（Issue #37）。"""
    content = Path("webui/static/lib/purify.min.js").read_text(encoding="utf-8")

    assert "stub" not in content.lower(), "purify.min.js がプレースホルダのスタブのままです"
    assert len(content) > 10_000, (
        f"purify.min.js の内容が短すぎます（{len(content)} bytes）。"
        "実物のライブラリではない可能性があります"
    )


def test_marked_local_file_is_not_a_placeholder_stub():
    """ローカル同梱した marked.min.js が実際に動作するライブラリであることを確認する（Issue #36）。

    プレースホルダ的なスタブ（`marked.parse` を呼んでも何も返さない実装）ではなく、
    実際の marked のコード（Tokenizer/Lexer/Parser 等の実装）を含んでいることを検証する。
    """
    content = Path("webui/static/lib/marked.min.js").read_text(encoding="utf-8")

    assert "stub" not in content.lower(), "marked.min.js がプレースホルダのスタブのままです"
    # 実物の marked は Tokenizer/Lexer/Parser の実装を含み、数十KB規模になる。
    # スタブは 1KB 未満のごく短い内容だった。
    assert len(content) > 10_000, (
        f"marked.min.js の内容が短すぎます（{len(content)} bytes）。"
        "実物のライブラリではない可能性があります"
    )
