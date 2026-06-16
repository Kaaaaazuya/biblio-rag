"""アップロード補助のメタデータ書き出しテスト（S3 不要）。"""

import json

from workers import upload


def test_write_meta_creates_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "BOOKS_DIR", tmp_path)
    path = upload._write_meta("mybook", "書名", "著者名")
    assert path == tmp_path / "mybook.meta.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"title": "書名", "author": "著者名"}
