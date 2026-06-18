"""raw PDF をオブジェクトストレージ（MinIO/S3）にアップロードする補助 CLI。

将来は WebUI からのアップロードに置き換わる想定。手元の PDF を投入する用。
アップロード時に --title/--author を渡すと、S3 object metadata に書誌情報を
記録する（② チャンク層が参照する。日本語は URL エンコード済みで格納）。

CLI:
  uv run python -m workers.upload book.pdf --title "書名" --author "著者名"
  uv run python -m workers.upload a.pdf b.pdf            # 複数可（メタは別途用意）
  → s3://<bucket>/raw/<ファイル名> に配置（book_id = ファイル名 stem）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

from workers.storage import RAW_PREFIX, ObjectStore


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="raw PDF を S3(MinIO) にアップロード")
    parser.add_argument("paths", nargs="+", help="アップロードする PDF")
    parser.add_argument("--title", help="書名（S3 object metadata に記録）")
    parser.add_argument("--author", help="著者名（--title と併用）")
    args = parser.parse_args(argv)

    if (args.title is None) != (args.author is None):
        print("--title と --author は両方指定してください", file=sys.stderr)
        return 1
    if args.title is not None and len(args.paths) != 1:
        print("--title/--author 指定時は PDF を 1 つだけ指定してください", file=sys.stderr)
        return 1

    metadata = None
    if args.title is not None:
        # S3 object metadata は US-ASCII のみ → 日本語を URL エンコード
        metadata = {"title": quote(args.title), "author": quote(args.author)}

    store = ObjectStore()
    for arg in args.paths:
        path = Path(arg)
        if not path.is_file():
            print(f"見つかりません: {path}", file=sys.stderr)
            return 1
        key = f"{RAW_PREFIX}{path.name}"
        store.put_file(path, key, metadata=metadata)
        print(f"{path} -> s3://{store.bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
