"""① 抽出層: テキスト埋め込み済み日本語 PDF → 構造つき Markdown。

方針（design.md / CLAUDE.md より）:
  - ブロック(行)単位で取得し座標で整列 → 読み順を安定化
  - ヘッダ/フッタ（ページ番号・繰り返し書名）を位置とパターン・繰り返しで除去
  - 段落内の改行を結合（日本語なので空白なし）、空行=段落区切り
  - 見出し検出: 最頻フォントサイズ=本文、それより大きい=見出しの相対判定。
    フォントの大きい順にレベル付け（# / ## / ###）。「第◯章」パターンを併用。

CLI: uv run python -m workers.extract            # books/raw/*.pdf → books/normalized/*.md
     uv run python -m workers.extract a.pdf b.pdf
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

RAW_DIR = Path("books/raw")
OUT_DIR = Path("books/normalized")

# ヘッダ/フッタ帯（ページ高に対する割合）
HEADER_BAND = 0.07
FOOTER_BAND = 0.93
# 「- 1 -」「12」「‐ 3 ‐」等のページ番号らしさ
PAGE_NUM_RE = re.compile(r"^[\s\-‐－—~〜ー]*\d{1,4}[\s\-‐－—~〜ー]*$")
# 章見出しの定型パターン（フォント情報が乏しい場合のフォールバック）
CHAPTER_RE = re.compile(r"^第[0-9一二三四五六七八九十百千]+[章編部]")


class _Line:
    __slots__ = ("page", "text", "x0", "y0", "y1", "size")

    def __init__(self, page, text, x0, y0, y1, size):
        self.page = page
        self.text = text
        self.x0 = x0
        self.y0 = y0
        self.y1 = y1
        self.size = size


def _collect_lines(doc) -> tuple[list[_Line], list[float]]:
    """全ページの行を収集する。戻り値: (行リスト, ページ高リスト)。"""
    lines: list[_Line] = []
    heights: list[float] = []
    for pno, page in enumerate(doc):
        heights.append(page.rect.height)
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block:  # 画像ブロック等
                continue
            for line in block["lines"]:
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                size = round(max(s["size"] for s in spans), 1)
                x0, y0, _, y1 = line["bbox"]
                lines.append(_Line(pno, text, x0, y0, y1, size))
    return lines, heights


def _strip_header_footer(lines: list[_Line], heights: list[float]) -> list[_Line]:
    """ヘッダ/フッタ帯にあり、ページ番号 or 複数ページで繰り返す行を除去する。"""
    n_pages = len(heights)
    in_band: list[_Line] = []
    for ln in lines:
        h = heights[ln.page]
        if ln.y1 <= h * HEADER_BAND or ln.y0 >= h * FOOTER_BAND:
            in_band.append(ln)

    # 帯の中で「ページをまたいで繰り返すテキスト」を走り書き（書名ヘッダ等）
    def norm(t: str) -> str:
        return re.sub(r"\s+", "", t)

    band_counts = Counter(norm(ln.text) for ln in in_band)
    repeat_threshold = max(2, n_pages // 2 + 1)
    repeated = {t for t, c in band_counts.items() if c >= repeat_threshold}

    drop = set()
    for ln in in_band:
        if PAGE_NUM_RE.match(ln.text) or norm(ln.text) in repeated:
            drop.add(id(ln))
    return [ln for ln in lines if id(ln) not in drop]


def _body_size(lines: list[_Line]) -> float:
    """文字数で重み付けした最頻フォントサイズ=本文サイズ。"""
    weight: Counter[float] = Counter()
    for ln in lines:
        weight[ln.size] += len(ln.text)
    return weight.most_common(1)[0][0]


def _heading_levels(lines: list[_Line], body: float) -> dict[float, int]:
    """本文より大きいサイズを大きい順に # / ## / ... へ割り当てる。"""
    bigger = sorted({ln.size for ln in lines if ln.size > body * 1.15}, reverse=True)
    return {size: i + 1 for i, size in enumerate(bigger)}


def _modal_spacing(lines: list[_Line], body: float) -> float:
    """本文行の行送り（連続する本文行の y0 差の最頻値）。段落分割の基準に使う。"""
    deltas: Counter[float] = Counter()
    prev = None
    for ln in sorted(lines, key=lambda x: (x.page, x.y0, x.x0)):
        if abs(ln.size - body) < 0.6:
            if prev is not None and prev.page == ln.page:
                deltas[round(ln.y0 - prev.y0)] += 1
            prev = ln
        else:
            prev = None
    return deltas.most_common(1)[0][0] if deltas else body * 1.7


def extract_pdf_to_markdown(pdf_path: str | Path) -> str:
    """PDF を構造つき Markdown に変換して返す。"""
    with fitz.open(pdf_path) as doc:
        lines, heights = _collect_lines(doc)
    lines = _strip_header_footer(lines, heights)
    if not lines:
        return ""

    body = _body_size(lines)
    levels = _heading_levels(lines, body)
    para_gap = _modal_spacing(lines, body) * 1.4  # これを超える行間は段落区切り

    lines.sort(key=lambda x: (x.page, x.y0, x.x0))

    blocks: list[str] = []
    buf: list[str] = []  # 現在組み立て中の段落/見出しのテキスト
    buf_level: int | None = None  # None=本文段落、>=1=見出しレベル
    prev: _Line | None = None

    def flush():
        if buf:
            text = "".join(buf)  # 日本語: 行を空白なしで結合
            blocks.append("#" * buf_level + " " + text if buf_level else text)
            buf.clear()

    for ln in lines:
        level = levels.get(ln.size)
        if level is None and CHAPTER_RE.match(ln.text):
            level = min(2, 1 + (1 if levels else 0))  # パターンによる章見出しフォールバック
        # 折り返し（見出し）/ 行送り（本文）の許容ギャップ
        gap = ln.size * 1.6 if level is not None else para_gap
        adjacent = prev is not None and ln.page == prev.page and (ln.y0 - prev.y0) <= gap
        # 直前と種別が変わる or 離れていれば確定して新規開始
        if buf and (level != buf_level or not adjacent):
            flush()
        buf_level = level
        buf.append(ln.text)
        prev = ln
    flush()

    return "\n\n".join(blocks) + "\n"


def _cli(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] if argv else sorted(RAW_DIR.glob("*.pdf"))
    if not paths:
        print(f"PDF が見つかりません（{RAW_DIR}/*.pdf または引数で指定）", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in paths:
        md = extract_pdf_to_markdown(pdf)
        out = OUT_DIR / f"{pdf.stem}.md"
        out.write_text(md, encoding="utf-8")
        print(f"{pdf} -> {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
