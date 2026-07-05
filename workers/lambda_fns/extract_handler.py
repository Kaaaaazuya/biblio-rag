"""λ-extract: raw/{book_id}.pdf → normalized/{book_id}.md。"""

from __future__ import annotations

from pathlib import PurePosixPath

from workers.extract.extract import extract_pdf_to_markdown
from workers.storage import ObjectStore

from .events import s3_keys_from_event


def handler(event: dict, context: object = None) -> None:
    store = ObjectStore()
    for _bucket, key in s3_keys_from_event(event):
        book_id = PurePosixPath(key).stem
        md = extract_pdf_to_markdown(store.get_bytes(key))
        store.put_text(f"normalized/{book_id}.md", md)
