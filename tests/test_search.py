"""T5 検索の表示整形のユニットテスト（ライブサービス不要）。"""

from workers.search.search import _snippet, format_result


def test_snippet_strips_heading_prefix():
    text = "第一章 設計 > 1.1 目的\n本文のはじまりである。続きがある。"
    assert _snippet(text).startswith("本文のはじまりである")
    assert ">" not in _snippet(text)  # 見出し prefix は除かれる


def test_format_result_includes_source_and_score():
    rec = {
        "title": "テスト書",
        "chapter": "第一章 設計",
        "section": "1.1 目的",
        "page": None,
        "score": 0.8421,
        "text": "第一章 設計 > 1.1 目的\n本文である。",
    }
    out = format_result(1, rec)
    assert "[1]" in out
    assert "score=0.842" in out
    assert "テスト書 / 第一章 設計 > 1.1 目的" in out
    assert "p." not in out  # page が None のときは表示しない
    assert "本文である" in out


def test_format_result_shows_page_when_present():
    rec = {"title": "T", "chapter": None, "section": None, "page": 12, "score": 0.5, "text": "x"}
    assert "p.12" in format_result(2, rec)
