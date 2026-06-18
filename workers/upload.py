"""raw PDF をオブジェクトストレージ（MinIO/S3）にアップロードする補助 CLI。

将来は WebUI からのアップロードに置き換わる想定。手元の PDF を投入する用。
アップロード時に --title/--author を渡すと、S3 object metadata に書誌情報を
記録する（② チャンク層が参照する。日本語は URL エンコード済みで格納）。

CLI:
  uv run python -m workers.upload book.pdf --title "書名" --author "著者名"
  uv run python -m workers.upload a.pdf b.pdf            # 複数可（メタは別途用意）
  uv run python -m workers.upload --book-id mybook --title "書名" --author "著者名"
    # PDF 再アップロードなしでメタデータだけ更新（S3 copy_object）
  → s3://<bucket>/raw/<ファイル名> に配置（book_id = ファイル名 stem）
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

from workers.storage import RAW_PREFIX, ObjectStore


def _find_raw_key(store: ObjectStore, book_id: str) -> str | None:
    """S3 の実キーを NFC 正規化して book_id と照合する。

    macOS はファイル名を NFD で保存するため、アップロード時のキーが NFD になる場合がある。
    ターミナル入力は NFC であることが多く、文字列比較でミスマッチが起きる。
    両辺を NFC に揃えて一致するキーを返す。
    """
    target = unicodedata.normalize("NFC", book_id)
    for key in store.list_pdfs(RAW_PREFIX):
        if unicodedata.normalize("NFC", Path(key).stem) == target:
            return key
    return None


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="raw PDF を S3(MinIO) にアップロード")
    parser.add_argument("paths", nargs="*", help="アップロードする PDF")
    parser.add_argument("--title", help="書名（S3 object metadata に記録）")
    parser.add_argument("--author", help="著者名（--title と併用）")
    parser.add_argument(
        "--book-id",
        help="既存オブジェクトのメタデータのみ更新（PDF 再アップロードなし）",
    )
    args = parser.parse_args(argv)

    if (args.title is None) != (args.author is None):
        print("--title と --author は両方指定してください", file=sys.stderr)
        return 1

    store = ObjectStore()

    # --book-id のみ: 既存 S3 オブジェクトのメタデータを copy_object で上書き
    if args.book_id:
        if args.paths:
            print("--book-id 指定時は PDF パスを指定しないでください", file=sys.stderr)
            return 1
        if not args.title:
            print("--book-id には --title/--author も必要です", file=sys.stderr)
            return 1
        key = _find_raw_key(store, args.book_id)
        if key is None:
            print(
                f"見つかりません: raw/{args.book_id}.pdf（MinIO に raw PDF が存在するか確認）",
                file=sys.stderr,
            )
            return 1
        metadata = {"title": quote(args.title), "author": quote(args.author)}
        try:
            store.client.copy_object(
                Bucket=store.bucket,
                CopySource={"Bucket": store.bucket, "Key": key},
                Key=key,
                Metadata=metadata,
                MetadataDirective="REPLACE",
            )
        except Exception as e:  # noqa: BLE001
            print(f"メタデータ更新失敗: {e}", file=sys.stderr)
            return 1
        print(f"メタデータ更新: s3://{store.bucket}/{key} (title={args.title})")
        return 0

    if not args.paths:
        print("PDF ファイルを指定してください", file=sys.stderr)
        return 1
    if args.title is not None and len(args.paths) != 1:
        print("--title/--author 指定時は PDF を 1 つだけ指定してください", file=sys.stderr)
        return 1

    metadata = None
    if args.title is not None:
        # S3 object metadata は US-ASCII のみ → 日本語を URL エンコード
        metadata = {"title": quote(args.title), "author": quote(args.author)}

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
