"""静的ファイル（HTML・JS）のセキュリティチェックのテスト。

外部 CDN スクリプトのバージョン固定と SRI（Subresource Integrity）の検証。
"""

from __future__ import annotations

import re
from pathlib import Path


def _read_html(path: str) -> str:
    """HTML ファイルを読み込む。"""
    return Path(path).read_text(encoding="utf-8")


def test_chat_html_marked_has_integrity_or_local():
    """chat.html の marked スクリプト は integrity 属性か、またはローカルパスからロードされている。"""
    html = _read_html("webui/static/chat.html")

    # marked スクリプトタグを探す
    marked_script_pattern = r'<script\s+([^>]*)\s*src\s*=\s*["\']([^"\']*marked[^"\']*)["\']([^>]*)>'
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
        f"marked スクリプトに integrity 属性がありません。"
        f"タグ全体: <script {full_tag}>"
    )

    # crossorigin="anonymous" が設定されていることを確認
    assert re.search(r'crossorigin\s*=\s*["\']anonymous["\']', full_tag), (
        f"marked スクリプトに crossorigin=\"anonymous\" が設定されていません。"
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
