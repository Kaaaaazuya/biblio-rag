"""WebUI バックエンドのテスト（presign は署名のみでオフライン・MinIO 不要）。"""

import json

from starlette.testclient import TestClient

from webui import server

client = TestClient(server.app)


def test_presign_returns_url_and_key():
    res = client.post("/api/presign", json={"filename": "本の名前.pdf"})
    assert res.status_code == 200
    data = res.json()
    assert data["key"] == "raw/本の名前.pdf"
    assert data["book_id"] == "本の名前"
    assert data["url"].startswith("http")  # presigned URL


def test_presign_rejects_non_pdf():
    res = client.post("/api/presign", json={"filename": "evil.exe"})
    assert res.status_code == 400


def test_presign_sanitizes_path_traversal():
    # ディレクトリ要素は除去され raw/ 直下になる
    res = client.post("/api/presign", json={"filename": "../../etc/passwd.pdf"})
    assert res.status_code == 200
    assert res.json()["key"] == "raw/passwd.pdf"


def test_meta_writes_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BOOKS_DIR", tmp_path)
    res = client.post("/api/meta", json={"book_id": "mybook", "title": "書名", "author": "著者"})
    assert res.status_code == 200
    data = json.loads((tmp_path / "mybook.meta.json").read_text(encoding="utf-8"))
    assert data == {"title": "書名", "author": "著者"}


def test_meta_requires_title_author(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BOOKS_DIR", tmp_path)
    res = client.post("/api/meta", json={"book_id": "x", "title": "", "author": "a"})
    assert res.status_code == 400


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "書籍アップロード" in res.text


def test_ingest_bad_book_id():
    res = client.post("/api/ingest", json={"book_id": ""})
    assert res.status_code == 400


def test_ingest_status_unknown():
    res = client.get("/api/ingest/nonexistent/status")
    assert res.status_code == 200
    assert res.json()["status"] == "unknown"


def test_ingest_sets_pending_status(monkeypatch):
    # パイプライン本体は呼ばずにステータス遷移だけ確認
    monkeypatch.setattr(server, "_run_pipeline", lambda book_id: None)
    client.post("/api/ingest", json={"book_id": "testbook"})
    res = client.get("/api/ingest/testbook/status")
    assert res.status_code == 200
    assert res.json()["status"] in ("pending", "done")  # BackgroundTask 完了タイミング依存
