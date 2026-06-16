"""① 抽出層のユニットテスト（PDF → Markdown）。

入力は著作権フリーのテスト PDF（tests/fixtures/sample_book.pdf）。
存在しなければ make_fixture から再生成する。
"""

import importlib.util
from pathlib import Path

import pytest

from workers.extract import extract_pdf_to_markdown

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PDF = FIXTURE_DIR / "sample_book.pdf"


def _ensure_fixture() -> Path:
    if not PDF.exists():
        spec = importlib.util.spec_from_file_location("make_fixture", FIXTURE_DIR / "make_fixture.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.build()
    return PDF


@pytest.fixture(scope="module")
def md() -> str:
    return extract_pdf_to_markdown(_ensure_fixture())


def test_title_is_single_h1(md):
    # 折り返された書名が1つの # 見出しに結合されている
    assert "# RAG 取り込みパイプライン設計ノート" in md
    assert md.count("\n# ") + md.startswith("# ") * 1 == 1  # H1 は1つだけ


def test_chapter_and_section_levels(md):
    # 章=##、節=###（相対フォントサイズによる階層）
    assert "## 第一章 設計の前提" in md
    assert "## 第二章 抽出の詳細" in md
    assert "### 1.1 目的とスコープ" in md
    assert "### 2.2 正規化" in md


def test_header_footer_removed(md):
    # 繰り返しヘッダ（書名・空白入り）とフッタ（ページ番号）が除去されている
    assert "RAG 取り込みパイプライン 設計ノート" not in md  # ヘッダの空白入り異形
    assert "- 1 -" not in md and "- 2 -" not in md


def test_paragraph_lines_joined(md):
    # PDF 上で複数行に折り返された一文が、改行なしで連結されている
    assert "検索対象となるベクトルインデックスを構築する取り込みパイプラインの設計をまとめたものである" in md


def test_paragraph_separation(md):
    # 段落は空行で区切られる（本文段落が独立した行として存在）
    paras = [b.strip() for b in md.split("\n\n") if b.strip() and not b.startswith("#")]
    assert len(paras) >= 6  # 本文段落が複数復元されている
    # 各本文段落の内部に改行が残っていない（段落内の不要改行が結合済み）
    assert all("\n" not in p for p in paras)


def test_reading_order(md):
    # 章の出現順が保たれている
    assert md.index("第一章") < md.index("第二章")
    assert md.index("1.1 目的") < md.index("2.1 読み順")
