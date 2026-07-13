"""② チャンク層: 構造つき Markdown → チャンク JSONL。

方針（ADR 0007 / design.md）:
  - 文字数ベース分割（既定 800 字・overlap 120 字、設定で可変）
  - 句点「。」優先で文の途中で切らない
  - 見出し境界を尊重（見出しをまたいでチャンクしない）
  - 見出し階層を prefix として本文頭に付与（例「第一章 設計の前提 > 1.1 目的とスコープ」）
  - メタデータ付与: book_id / title / author / chapter / section / page / text
    （title・author は必須。S3 object metadata から取得）

CLI: uv run python -m workers.chunk            # S3 normalized/ の .md をすべて処理
     uv run python -m workers.chunk mybook --size 600 --overlap 100  # book_id 指定
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .base import Chunker

NORM_PREFIX = "normalized/"
CHUNKS_PREFIX = "chunks/"

DEFAULT_SIZE = 800
DEFAULT_OVERLAP = 120

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_RE = re.compile(r"^<!-- page:(\d+) -->$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _split_prose(text: str, size: int, overlap: int) -> list[str]:
    """文字数 + 句点優先 + overlap でテキストを分割する（コード外の散文用）。"""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            dot = text.rfind("。", start, end)
            if dot != -1 and (dot + 1 - start) >= size * 0.5:
                end = dot + 1
            else:
                fwd = text.find("。", end)
                if fwd != -1 and (fwd + 1 - start) <= size * 1.5:
                    end = fwd + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    """文字数 + 句点優先 + コードフェンス保護で分割する。

    フェンス（``` or ~~~）で囲まれたコードブロックは原子単位として扱い、
    サイズを超えても分割しない。散文部分のみ _split_prose を適用する。
    """
    text = text.strip()
    if not text:
        return []

    # テキストをコードブロックと散文に分割
    # コードブロックは ``` から ``` までを1まとまりとして保持
    result: list[str] = []
    prose_buf = ""

    lines = text.splitlines(keepends=True)
    in_fence = False
    fence_buf = ""

    for line in lines:
        stripped = line.rstrip("\n")
        if _FENCE_RE.match(stripped):
            if not in_fence:
                # フェンス開始: 直前の散文をフラッシュ
                if prose_buf.strip():
                    result.extend(_split_prose(prose_buf, size, overlap))
                    prose_buf = ""
                in_fence = True
                fence_buf = line
            else:
                # フェンス終了: コードブロックをそのまま追加
                fence_buf += line
                result.append(fence_buf.rstrip())
                fence_buf = ""
                in_fence = False
        elif in_fence:
            fence_buf += line
        else:
            prose_buf += line

    # 未クローズフェンス（不完全なコードブロック）はそのまま追加
    if fence_buf.strip():
        result.append(fence_buf.rstrip())
    if prose_buf.strip():
        result.extend(_split_prose(prose_buf, size, overlap))

    return result


def _parse_sections(md: str) -> list[tuple[list[str], str, int | None]]:
    """Markdown を (見出しパス, 本文, 開始ページ) のセクション列に分解する。

    見出しパスは書名(レベル1)を除く章・節（レベル2以降）の並び。
    開始ページは <!-- page:N --> マーカーから取得（無ければ None）。
    """
    sections: list[tuple[list[str], str, int | None]] = []
    stack: dict[int, str] = {}
    body: list[str] = []
    cur_page: int | None = None
    section_page: int | None = None  # 現セクションの開始ページ

    def flush() -> None:
        if body:
            text = "\n".join(body).strip()
            if text:
                path = [stack[lv] for lv in sorted(stack) if lv >= 2]
                sections.append((path, text, section_page))
            body.clear()

    in_fence = False
    for raw in md.splitlines():
        line = raw.strip()

        # コードフェンス状態の追跡（フェンス内は見出し判定・空行スキップをしない）
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if not body:
                section_page = cur_page
            body.append(raw)
            continue

        if in_fence:
            # フェンス内: 空行を保持し、見出しとして解釈しない
            body.append(raw)
            continue

        if not line:
            continue

        m_page = _PAGE_RE.match(line)
        if m_page:
            cur_page = int(m_page.group(1))
            continue

        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack[level] = m.group(2).strip()
            for deeper in [lv for lv in stack if lv > level]:
                del stack[deeper]
            section_page = cur_page
        else:
            if not body:
                section_page = cur_page
            body.append(line)
    flush()
    return sections


class HeuristicChunker(Chunker):
    """ADR 0007 のヒューリスティック分割（文字数 + 句点 + 見出し境界尊重）。"""

    def __init__(self, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP) -> None:
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
        for path, body, page in _parse_sections(md):
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
                        "page": page,
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


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="② チャンク: Markdown → JSONL")
    parser.add_argument("book_ids", nargs="*", help="book_id（省略時は S3 normalized/ を一括処理）")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--force", action="store_true", help="処理済み(.jsonl)も再生成（洗い替え）")
    args = parser.parse_args(argv)

    from workers.storage import ObjectStore

    store = ObjectStore()
    chunker = HeuristicChunker(args.size, args.overlap)

    if args.book_ids:
        book_ids = args.book_ids
    else:
        norm_keys = [k for k in store.list_keys(NORM_PREFIX) if k.endswith(".md")]
        if not norm_keys:
            print(f"S3 に Markdown がありません（{store.bucket}/{NORM_PREFIX}）", file=sys.stderr)
            return 1
        book_ids = [Path(k).stem for k in norm_keys]

    skipped: list[str] = []
    for book_id in book_ids:
        norm_key = f"{NORM_PREFIX}{book_id}.md"
        chunks_key = f"{CHUNKS_PREFIX}{book_id}.jsonl"
        if not args.force and not args.book_ids and store.key_exists(chunks_key):
            print(f"スキップ（既存）: {chunks_key}")
            continue
        try:
            md = store.get_text(norm_key)
            meta = store.get_meta(f"raw/{book_id}.pdf")
            meta["book_id"] = book_id
            records = chunker.chunk(md, meta)
        except ValueError as e:
            print(f"スキップ {book_id}: {e}", file=sys.stderr)
            skipped.append(book_id)
            continue
        store.put_jsonl(chunks_key, records)
        print(
            f"s3://{store.bucket}/{norm_key} -> s3://{store.bucket}/{chunks_key}"
            f" ({len(records)} chunks)"
        )

    if skipped:
        print(
            f"\nメタデータ未整備で {len(skipped)} 冊スキップ: {', '.join(skipped)}\n"
            "  対処: 以下のコマンドで S3 object metadata に書誌情報を登録してください:\n"
            + "".join(
                f"    uv run python -m workers.upload --book-id {bid}"
                f' --title "書名" --author "著者名"\n'
                for bid in skipped
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
