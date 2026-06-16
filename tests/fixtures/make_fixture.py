"""テスト用の日本語 PDF フィクスチャを生成する。

抽出器(workers/extract)の検証用。文学的内容ではなく**構造**を作り込むのが目的:
  - 書名(最大フォント) / 章見出し(大) / 節見出し(中) / 本文(最頻サイズ) のフォント差
  - 「第◯章」パターンの見出し
  - 段落内の改行（PyMuPDF が複数行ブロックとして返す → 改行結合を検証）
  - 段落間の空行
  - 全ページ共通のヘッダ(書名)とフッタ(ページ番号) → ヘッダ/フッタ除去を検証
  - 複数ページ

本文は著作権フリーのオリジナル文章（このリポジトリの作者が記述）。
実行: uv run python tests/fixtures/make_fixture.py
出力: tests/fixtures/sample_book.pdf
"""

from pathlib import Path

from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
)

FONT = "HeiseiMin-W3"  # reportlab 同梱の日本語 CID フォント（追加DL不要）
BOOK_TITLE = "RAG 取り込みパイプライン 設計ノート"
OUT = Path(__file__).parent / "sample_book.pdf"

# フォントサイズに明確な差をつけ、相対判定（最頻=本文・大=見出し）を検証可能にする
STYLES = {
    "title": ParagraphStyle("title", fontName=FONT, fontSize=24, leading=30, spaceAfter=20),
    "h1": ParagraphStyle("h1", fontName=FONT, fontSize=18, leading=24, spaceBefore=16, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName=FONT, fontSize=14, leading=20, spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=18, spaceAfter=10),
}

# (スタイル名, テキスト) の並び。本文は意図的に長くして行折り返し＆複数ページにする。
CONTENT = [
    ("title", BOOK_TITLE),
    ("h1", "第一章 設計の前提"),
    ("h2", "1.1 目的とスコープ"),
    ("body",
     "本書はテキスト埋め込み済みの日本語書籍を入力として、検索対象となるベクトル"
     "インデックスを構築する取り込みパイプラインの設計をまとめたものである。"
     "対象は抽出から格納までであり、回答生成そのものはここでは扱わない。"
     "各層は中間成果物をファイルとして残し、後段の再実行コストを最小化する。"),
    ("body",
     "層を分離する理由は明快である。抽出ロジックの変更は最も重い処理のやり直しを"
     "招くため、抽出結果を正本として保存しておけば、チャンク分割や埋め込みの調整は"
     "その正本を読み直すだけで済む。これにより試行錯誤の速度が大きく向上する。"),
    ("h2", "1.2 実行基盤の方針"),
    ("body",
     "開発はローカルで完結させ、本番はクラウドへ移行する二段構えとする。"
     "両環境で中間成果物の形式を共通化することで、移行時の差分を埋め込みモデルと"
     "接続先の二点だけに閉じ込めることができる。スキーマや分割ロジックは共通である。"),
    ("h1", "第二章 抽出の詳細"),
    ("h2", "2.1 読み順の安定化"),
    ("body",
     "段組みや脚注を含む紙面では、素朴な抽出では読み順が乱れることがある。"
     "ブロック単位で取得し、座標に基づいて整列させることで、人間が読む順序に近い"
     "テキスト列を得る。これが後段のチャンク分割の品質を左右する。"),
    ("body",
     "見出しの検出には、紙面で最も多く使われるフォントサイズを本文とみなし、"
     "それより大きいものを見出しとする相対判定を用いる。加えて、章を表す定型の"
     "表現を併用することで、フォント情報が乏しい場合でも構造を復元しやすくなる。"),
    ("h2", "2.2 正規化"),
    ("body",
     "段落の途中で入った改行は結合し、空行を段落の区切りとして扱う。"
     "ページ番号や繰り返し現れる書名などのヘッダ・フッタは、位置とパターンに"
     "基づいて取り除く。こうして得た整形済みテキストを正本として保存する。"),
]


def build() -> Path:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))

    def on_page(canvas, doc):
        # 全ページ共通のヘッダ（書名）とフッタ（ページ番号）
        canvas.saveState()
        canvas.setFont(FONT, 8)
        w, h = A5
        canvas.drawCentredString(w / 2, h - 10 * mm, BOOK_TITLE)          # ヘッダ
        canvas.drawCentredString(w / 2, 8 * mm, f"- {doc.page} -")        # フッタ(ページ番号)
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(OUT), pagesize=A5,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="t", frames=[frame], onPage=on_page)])

    story = []
    for style, text in CONTENT:
        story.append(Paragraph(text, STYLES[style]))
        if style == "title":
            story.append(Spacer(1, 12 * mm))
    doc.build(story)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
