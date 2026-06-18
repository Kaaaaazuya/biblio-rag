"""アップロード補助の CLI テスト（S3 不要）。"""

from unittest.mock import MagicMock, patch

from workers import upload


def test_upload_passes_metadata_to_store(tmp_path):
    """--title/--author が S3 object metadata として渡ることを確認。"""
    pdf = tmp_path / "mybook.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    mock_store = MagicMock()
    with patch("workers.upload.ObjectStore", return_value=mock_store):
        rc = upload._cli([str(pdf), "--title", "書名", "--author", "著者名"])

    assert rc == 0
    call_kwargs = mock_store.put_file.call_args
    metadata = call_kwargs.kwargs.get("metadata") or call_kwargs.args[2]
    assert metadata is not None
    # 値は URL エンコード済み（ASCII）
    from urllib.parse import unquote

    assert unquote(metadata["title"]) == "書名"
    assert unquote(metadata["author"]) == "著者名"
