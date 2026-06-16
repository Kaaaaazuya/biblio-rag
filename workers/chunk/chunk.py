"""② チャンク層: 構造つき Markdown → チャンク JSONL。

方針（ADR 0007 / design.md）:
  - 文字数ベース分割（既定 500 字・overlap 80 字、設定で可変）
  - 句点「。」優先で文の途中で切らない
  - 見出し境界を尊重（見出しをまたいでチャンクしない）
  - 見出し階層を prefix として本文頭に付与（例「第一章 設計の前提 > 1.1 目的とスコープ」）
  - メタデータ付与: book_id / title / author / chapter / section / page / text
    （title・author は必須。page は MVP では null・表示の余地として列は残す）

CLI: uv run python -m workers.chunk            # books/normalized/*.md → books/chunks/*.jsonl
     uv run python -m workers.chunk a.md --size 600 --overlap 100
メタデータは books/<stem>.meta.json（{"title": ..., "author": ...}）から読む。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .base import Chunker

NORM_DIR = Path("books/normalized")
OUT_DIR = Path("books/chunks")
BOOKS_DIR = Path("books")

DEFAULT_SIZE = 500
DEFAULT_OVERLAP = 80

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    """文字数 + 句点優先 + overlap でテキストを分割する。"""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # まず end 以前の最後の「。」で切る（極小チャンクは避ける）
            dot = text.rfind("。", start, end)
            if dot != -1 and (dot + 1 - start) >= size * 0.5:
                end = dot + 1
            else:
                # 近傍後方に「。」があれば少しだけ延ばして文を切らない
                fwd = text.find("。", end)
                if fwd != -1 and (fwd + 1 - start) <= size * 1.5:
                    end = fwd + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)  # 必ず前進させる
    return chunks


def _parse_sections(md: str) -> list[tuple[list[str], str]]:
    """Markdown を (見出しパス, 本文) のセクション列に分解する。

    見出しパスは書名(レベル1)を除く章・節（レベル2以降）の並び。
    """
    sections: list[tuple[list[str], str]] = []
    stack: dict[int, str] = {}
    body: list[str] = []

    def flush() -> None:
        if body:
            text = "\n".join(body).strip()
            if text:
                path = [stack[lv] for lv in sorted(stack) if lv >= 2]
                sections.append((path, text))
            body.clear()

    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack[level] = m.group(2).strip()
            for deeper in [lv for lv in stack if lv > level]:
                del stack[deeper]
        else:
            body.append(line)
    flush()
    return sections


class HeuristicChunker(Chunker):
    """ADR 0007 のヒューリスティック分割（文字数 + 句点 + 見出し境界尊重）。"""

    def __init__(self, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP):
        if overlap >= size:
            raise ValueError("overlap は size より小さくしてください")
        self.size = size
        self.overlap = overlap

    def chunk(self, md: str, meta: dict) -> list[dict]:
        """meta には book_id / title / author が必須。"""
        for key in ("book_id", "title", "author"):
            if not meta.get(key):
                raise ValueError(
                    f"メタデータ '{key}' は必須です（title・author はサイドカー JSON で指定）"
                )

        records: list[dict] = []
        idx = 0
        for path, body in _parse_sections(md):
            chapter = path[0] if path else None
            section = path[1] if len(path) > 1 else None
            prefix = " > ".join(path)
            for piece in _split_text(body, self.size, self.overlap):
                text = f"{prefix}\n{piece}" if prefix else piece
                records.append(
                    {
                        "book_id": meta["book_id"],
                        "chunk_index": idx,
                        "title": meta["title"],
                        "author": meta["author"],
                        "chapter": chapter,
                        "section": section,
                        "page": None,  # MVP では未取得（列は将来の表示用に残す）
                        "text": text,
                    }
                )
                idx += 1
        return records


def chunk_markdown(
    md: str,
    meta: dict,
    size: int = DEFAULT_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[dict]:
    """互換ヘルパー: HeuristicChunker の薄いラッパー。"""
    return HeuristicChunker(size, overlap).chunk(md, meta)


def _load_meta(stem: str) -> dict:
    """books/<stem>.meta.json から title / author を読み、book_id を補う。"""
    meta_path = BOOKS_DIR / f"{stem}.meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"メタデータが見つかりません: {meta_path}（{{'title':..., 'author':...}} を用意）"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("book_id", stem)
    return meta


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="② チャンク: Markdown → JSONL")
    parser.add_argument("paths", nargs="*", help="対象 .md（省略時は books/normalized/*.md）")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--force", action="store_true", help="処理済み(.jsonl)も再生成（洗い替え）")
    args = parser.parse_args(argv)

    is_batch = not args.paths
    paths = [Path(p) for p in args.paths] if args.paths else sorted(NORM_DIR.glob("*.md"))
    if not paths:
        print(f"Markdown が見つかりません（{NORM_DIR}/*.md または引数で指定）", file=sys.stderr)
        return 1

    chunker = HeuristicChunker(args.size, args.overlap)  # size/overlap はここで検証
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    for md_path in paths:
        out = OUT_DIR / f"{md_path.stem}.jsonl"
        # 既定: 一括実行で既存の .jsonl はスキップ（--force で洗い替え）
        if is_batch and out.exists() and not args.force:
            print(f"スキップ（既存）: {out.name}")
            continue
        try:
            meta = _load_meta(md_path.stem)
            records = chunker.chunk(md_path.read_text(encoding="utf-8"), meta)
        except (FileNotFoundError, ValueError) as e:
            # メタ未整備の本は止めずにスキップ（他の本は処理する）
            print(f"スキップ {md_path.name}: {e}", file=sys.stderr)
            skipped.append(md_path.stem)
            continue
        with out.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{md_path} -> {out} ({len(records)} chunks)")

    if skipped:
        print(
            f"\nメタデータ未整備で {len(skipped)} 冊スキップ: {', '.join(skipped)}\n"
            "  対処: books/<book_id>.meta.json を用意するか、アップロード時に\n"
            "  `workers.upload <pdf> --title ... --author ...` を指定して再実行してください",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
