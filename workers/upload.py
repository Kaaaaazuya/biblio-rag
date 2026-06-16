"""raw PDF をオブジェクトストレージ（MinIO/S3）にアップロードする補助 CLI。

将来は WebUI からのアップロードに置き換わる想定。手元の PDF を投入する用。

CLI: uv run python -m workers.upload path/to/book.pdf [別の.pdf ...]
     → s3://<bucket>/raw/<ファイル名> に配置（book_id = ファイル名 stem）
"""

from __future__ import annotations

import sys
from pathlib import Path

from workers.storage import RAW_PREFIX, ObjectStore


def _cli(argv: list[str]) -> int:
    if not argv:
        print("アップロードする PDF を指定してください", file=sys.stderr)
        return 1
    store = ObjectStore()
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            print(f"見つかりません: {path}", file=sys.stderr)
            return 1
        key = f"{RAW_PREFIX}{path.name}"
        store.put_file(path, key)
        print(f"{path} -> s3://{store.bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
