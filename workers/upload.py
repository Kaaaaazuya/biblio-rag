"""raw PDF をオブジェクトストレージ（MinIO/S3）にアップロードする補助 CLI。

将来は WebUI からのアップロードに置き換わる想定。手元の PDF を投入する用。
アップロード時に --title/--author を渡すと、② チャンクに必須のメタデータ
（books/<book_id>.meta.json）も同時に書き出す。

CLI:
  uv run python -m workers.upload book.pdf --title "書名" --author "著者名"
  uv run python -m workers.upload a.pdf b.pdf            # 複数可（メタは別途用意）
  → s3://<bucket>/raw/<ファイル名> に配置（book_id = ファイル名 stem）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from workers.storage import RAW_PREFIX, ObjectStore

BOOKS_DIR = Path("books")


def _write_meta(stem: str, title: str, author: str) -> Path:
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    path = BOOKS_DIR / f"{stem}.meta.json"
    path.write_text(
        json.dumps({"title": title, "author": author}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="raw PDF を S3(MinIO) にアップロード")
    parser.add_argument("paths", nargs="+", help="アップロードする PDF")
    parser.add_argument("--title", help="書名（指定すると meta.json も書く）")
    parser.add_argument("--author", help="著者名（--title と併用）")
    args = parser.parse_args(argv)

    if (args.title is None) != (args.author is None):
        print("--title と --author は両方指定してください", file=sys.stderr)
        return 1
    if args.title is not None and len(args.paths) != 1:
        print("--title/--author 指定時は PDF を 1 つだけ指定してください", file=sys.stderr)
        return 1

    store = ObjectStore()
    for arg in args.paths:
        path = Path(arg)
        if not path.is_file():
            print(f"見つかりません: {path}", file=sys.stderr)
            return 1
        key = f"{RAW_PREFIX}{path.name}"
        store.put_file(path, key)
        print(f"{path} -> s3://{store.bucket}/{key}")
        if args.title is not None:
            meta = _write_meta(path.stem, args.title, args.author)
            print(f"メタデータ -> {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
