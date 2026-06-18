"""① 抽出層: テキスト埋め込み済み日本語 PDF → 構造つき Markdown。

pymupdf4llm (pymupdf_rag バックエンド) をベースとし:
  - 見出し検出: IdentifyHeaders がフォントサイズ相対判定で #/##/### を付与
  - コードブロック検出: モノスペースフォントを自動検出し ``` フェンスで出力
  - ヘッダ/フッタ除去: margins パラメータでページ上下帯をクリップ
  - 段落行結合: pymupdf_rag は各 PDF 行を個別 block 化するため後処理で結合
  - ページマーカー: page_chunks で1ページずつ取得し <!-- page:N --> を注入

CLI: uv run python -m workers.extract            # S3(raw/) の PDF → books/normalized/*.md
     uv run python -m workers.extract a.pdf b.pdf # ローカル PDF を直接指定も可
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import fitz
import pymupdf4llm.helpers.pymupdf_rag as _rag

NORM_PREFIX = "normalized/"

HEADER_BAND = 0.07
FOOTER_BAND = 0.93

# コードフェンス・見出し・リスト等の「構造ブロック」の先頭パターン
_BLOCK_START = re.compile(r"^(#{1,6} |```|~~~|> |- |\* |\d+\. |<!--)")
# 文末にならない文字（この文字で終わる行は次行と結合する）
_SENTENCE_NOEND = re.compile(r"[^。！？」』…]\s*$")


def _join_wrapped_lines(text: str) -> str:
    """PDF 行折り返しを結合する。

    pymupdf_rag は各 PDF 行を個別段落（\\n\\n 区切り）として出力する。
    日本語では「。」で終わらない行は次行と連結する（句点優先ルール）。
    コードフェンス（``` で始まるブロック）は内部構造を保持したまま残す。
    """
    blocks = re.split(r"\n{2,}", text)
    result: list[str] = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if _BLOCK_START.match(block):
            result.append(block)
        elif result and not _BLOCK_START.match(result[-1]) and _SENTENCE_NOEND.search(result[-1]):
            result[-1] += block
        else:
            result.append(block)

    return "\n\n".join(result)


def extract_pdf_to_markdown(src: str | Path | bytes) -> str:
    """PDF（ファイルパス or バイト列）を構造つき Markdown に変換して返す。"""
    if isinstance(src, bytes | bytearray):
        doc = fitz.open(stream=bytes(src), filetype="pdf")
    else:
        doc = fitz.open(src)

    with doc:
        if doc.page_count == 0:
            return ""
        median_h = sorted(doc[i].rect.height for i in range(doc.page_count))[doc.page_count // 2]
        top_margin = median_h * HEADER_BAND
        bot_margin = median_h * (1 - FOOTER_BAND)
        margins = (0, top_margin, 0, bot_margin)

        try:
            indexed = list(enumerate(_rag.to_markdown(doc, page_chunks=True, margins=margins)))
        except ValueError:
            # pymupdf_rag が空テーブルセルで ValueError を出すライブラリのバグへの回避策。
            # ページ単位で再処理し、問題ページだけスキップする。
            indexed = []
            for i in range(doc.page_count):
                try:
                    result = _rag.to_markdown(doc, pages=[i], page_chunks=True, margins=margins)
                    indexed.append((i, result[0]))
                except ValueError:
                    pass

    parts: list[str] = []
    for i, page in indexed:
        text = _join_wrapped_lines(page["text"])
        if text.strip():
            parts.append(f"<!-- page:{i + 1} -->")
            parts.append(text)

    return "\n\n".join(parts) + "\n"


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="① 抽出: PDF → 構造つき Markdown")
    parser.add_argument("paths", nargs="*", help="ローカル PDF（省略時は S3 の raw/ を処理）")
    parser.add_argument("--force", action="store_true", help="処理済み(.md)も再生成（洗い替え）")
    args = parser.parse_args(argv)

    from workers.storage import ObjectStore

    store = ObjectStore()

    if args.paths:
        for arg in args.paths:
            pdf = Path(arg)
            stem = pdf.stem
            norm_key = f"{NORM_PREFIX}{stem}.md"
            md = extract_pdf_to_markdown(pdf)
            store.put_text(norm_key, md)
            print(f"{pdf} -> s3://{store.bucket}/{norm_key} ({len(md)} chars)")
        return 0

    keys = store.list_pdfs()
    if not keys:
        print(
            f"S3 に PDF がありません（{store.bucket}/raw/）。"
            "workers.upload で投入するか MinIO コンソールからアップロードしてください",
            file=sys.stderr,
        )
        return 1
    for key in keys:
        stem = Path(key).stem
        norm_key = f"{NORM_PREFIX}{stem}.md"
        if not args.force and store.key_exists(norm_key):
            print(f"スキップ（既存）: {norm_key}")
            continue
        md = extract_pdf_to_markdown(store.get_bytes(key))
        store.put_text(norm_key, md)
        print(f"s3://{store.bucket}/{key} -> s3://{store.bucket}/{norm_key} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
