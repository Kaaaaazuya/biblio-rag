"""WebUI バックエンドのテスト（presign は署名のみでオフライン・MinIO 不要）。"""

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from webui import server

client = TestClient(server.app)


def _mock_s3_client():
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "http://minio.local/signed-url"
    return mock_s3


def test_presign_returns_url_and_key():
    with (
        patch("webui.server._object_exists", return_value=False),
        patch("workers.config.s3_client", return_value=_mock_s3_client()),
    ):
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
    with (
        patch("webui.server._object_exists", return_value=False),
        patch("workers.config.s3_client", return_value=_mock_s3_client()),
    ):
        res = client.post("/api/presign", json={"filename": "../../etc/passwd.pdf"})
    assert res.status_code == 200
    assert res.json()["key"] == "raw/passwd.pdf"


def test_meta_updates_s3_metadata():
    from unittest.mock import MagicMock, patch
    from urllib.parse import unquote

    mock_s3 = MagicMock()
    with patch("workers.config.s3_client", return_value=mock_s3):
        res = client.post(
            "/api/meta", json={"book_id": "mybook", "title": "書名", "author": "著者"}
        )
    assert res.status_code == 200
    mock_s3.copy_object.assert_called_once()
    kw = mock_s3.copy_object.call_args.kwargs
    assert kw["MetadataDirective"] == "REPLACE"
    assert unquote(kw["Metadata"]["title"]) == "書名"
    assert unquote(kw["Metadata"]["author"]) == "著者"


def test_meta_requires_title_author():
    res = client.post("/api/meta", json={"book_id": "x", "title": "", "author": "a"})
    assert res.status_code == 400


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "書籍アップロード" in res.text


def test_ingest_bad_book_id():
    res = client.post("/api/ingest", json={"book_id": ""})
    assert res.status_code == 400


def test_ingest_status_unknown(monkeypatch):
    mock_store = MagicMock()
    mock_store.get_current_status.return_value = None
    monkeypatch.setattr(server, "_status_store", mock_store)

    res = client.get("/api/ingest/nonexistent/status")
    assert res.status_code == 200
    assert res.json()["status"] == "unknown"


def test_ingest_sets_pending_status(monkeypatch):
    # パイプライン本体は呼ばずにステータス遷移だけ確認
    monkeypatch.setattr(server, "_run_pipeline", lambda book_id: None)

    mock_store = MagicMock()
    mock_store.get_current_status.return_value = {
        "status": "pending",
        "chunks_processed": 0,
        "error_msg": None,
        "updated_at": None,
    }
    monkeypatch.setattr(server, "_status_store", mock_store)

    client.post("/api/ingest", json={"book_id": "testbook"})
    res = client.get("/api/ingest/testbook/status")
    assert res.status_code == 200
    assert res.json()["status"] in ("pending", "done")  # BackgroundTask 完了タイミング依存


# ============ Issue #16: アップロードエンドポイントの堅牢化 ============


def test_presign_rejects_overwrite_of_existing_object():
    """既存オブジェクトの上書きを防ぐ。presign 時に head_object で存在確認。"""
    from unittest.mock import MagicMock, patch

    mock_s3 = MagicMock()
    # head_object が success（オブジェクトが存在）
    mock_s3.head_object.return_value = {"ContentLength": 1000}

    with patch("workers.config.s3_client", return_value=mock_s3):
        res = client.post("/api/presign", json={"filename": "existing.pdf"})

    # 409 Conflict を返す（既存オブジェクト検出）
    assert res.status_code == 409
    assert "existing" in res.json()["detail"].lower() or "既に存在" in res.json()["detail"]
    # head_object が呼ばれたことを確認
    mock_s3.head_object.assert_called_once()


def test_presign_allows_new_object_when_not_exists():
    """新規オブジェクト（head_object で存在しない）は正常に presign URL を返す。"""
    from unittest.mock import MagicMock, patch

    mock_s3 = MagicMock()
    # head_object が NoSuchKey エラー（オブジェクトが存在しない）
    from botocore.exceptions import ClientError

    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    mock_s3.generate_presigned_url.return_value = "http://presigned.url/..."

    with patch("workers.config.s3_client", return_value=mock_s3):
        res = client.post("/api/presign", json={"filename": "newbook.pdf"})

    # 200 OK で presign URL を返す
    assert res.status_code == 200
    assert "url" in res.json()
    assert "key" in res.json()


def test_presign_rejects_oversized_content_length():
    """Content-Length > 500MB (524288000 bytes) は 413 Payload Too Large を返す。"""
    from unittest.mock import MagicMock, patch

    from botocore.exceptions import ClientError

    mock_s3 = MagicMock()
    # head_object が NoSuchKey エラー（オブジェクトが存在しない）
    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )

    with patch("workers.config.s3_client", return_value=mock_s3):
        # 600MB のサイズを指定
        res = client.post(
            "/api/presign",
            json={"filename": "huge.pdf", "content_length": 600 * 1024 * 1024},
        )

    # 413 Payload Too Large を返す
    assert res.status_code == 413
    assert "large" in res.json()["detail"].lower() or "大きすぎます" in res.json()["detail"]


def test_presign_accepts_valid_content_length():
    """Content-Length < 500MB は正常に presign URL を返す。"""
    from unittest.mock import MagicMock, patch

    from botocore.exceptions import ClientError

    mock_s3 = MagicMock()
    # head_object が NoSuchKey エラー（オブジェクトが存在しない）
    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    mock_s3.generate_presigned_url.return_value = "http://presigned.url/..."

    with patch("workers.config.s3_client", return_value=mock_s3):
        # 100MB のサイズ（上限以下）
        res = client.post(
            "/api/presign",
            json={"filename": "valid.pdf", "content_length": 100 * 1024 * 1024},
        )

    # 200 OK で presign URL を返す
    assert res.status_code == 200
    assert "url" in res.json()


def test_meta_rejects_oversized_content_length():
    """save_meta は Content-Length > 500MB を拒否（413）。"""
    from unittest.mock import MagicMock, patch

    mock_s3 = MagicMock()

    with patch("workers.config.s3_client", return_value=mock_s3):
        # 700MB のサイズを指定
        res = client.post(
            "/api/meta",
            json={
                "book_id": "bigbook",
                "title": "大きい本",
                "author": "著者",
                "content_length": 700 * 1024 * 1024,
            },
        )

    # 413 Payload Too Large を返す
    assert res.status_code == 413
    assert "large" in res.json()["detail"].lower() or "大きすぎます" in res.json()["detail"]
    # copy_object が呼ばれていないことを確認
    mock_s3.copy_object.assert_not_called()


def test_meta_accepts_valid_content_length():
    """save_meta は Content-Length < 500MB を受け入れる。"""
    from unittest.mock import MagicMock, patch

    mock_s3 = MagicMock()

    with patch("workers.config.s3_client", return_value=mock_s3):
        # 200MB のサイズ（上限以下）
        res = client.post(
            "/api/meta",
            json={
                "book_id": "validbook",
                "title": "正常な本",
                "author": "著者",
                "content_length": 200 * 1024 * 1024,
            },
        )

    # 200 OK で正常に保存
    assert res.status_code == 200
    mock_s3.copy_object.assert_called_once()
