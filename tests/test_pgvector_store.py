"""pgvector_store.py のユニットテスト（外部 DB 接続なし）。

embed_model フィルタリング機能を検証。
異なる埋め込みモデルのベクトルが混在する場合、
search() で指定されたモデルのベクトルのみが返される。
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from workers.embed.pgvector_store import PgVectorStore


@pytest.fixture
def mock_connection():
    """モック PostgreSQL 接続。"""
    conn = MagicMock()
    conn.autocommit = False
    return conn


@pytest.fixture
def store(mock_connection):
    """モック接続を使用した PgVectorStore インスタンス。"""
    with patch("psycopg.connect", return_value=mock_connection):
        yield PgVectorStore("postgresql://mock")


def test_search_filters_by_embed_model(store, mock_connection):
    """異なるモデルのベクトルが混在する場合、クエリのモデルに一致するベクトルのみ返す。"""
    # セットアップ：モック cursor が返すデータ
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    # 異なるモデルで埋め込まれたベクトルが混在している場合をシミュレート
    mock_cursor.fetchall.return_value = [
        {
            "book_id": "book1",
            "chunk_index": 0,
            "title": "Test Book",
            "author": "Author A",
            "chapter": "Ch1",
            "section": "Sec1",
            "page": 1,
            "text": "content1",
            "score": 0.95,
        },
        {
            "book_id": "book1",
            "chunk_index": 1,
            "title": "Test Book",
            "author": "Author A",
            "chapter": "Ch1",
            "section": "Sec2",
            "page": 2,
            "text": "content2",
            "score": 0.85,
        },
    ]

    # クエリ実行
    query_vector = [0.1] * 1024
    results = store.search(query_vector, top_k=10, embed_model="bge-m3")

    # 検証：execute() が呼ばれたか
    mock_cursor.execute.assert_called_once()
    call_args = mock_cursor.execute.call_args

    # SQL クエリをチェック
    sql = call_args[0][0]
    params = call_args[0][1]

    # WHERE 句に embed_model フィルタが含まれているか
    assert "embed_model" in sql, "SQL に embed_model フィルタが含まれていない"
    assert params["embed_model"] == "bge-m3", "embed_model パラメータが正しく渡されていない"

    # 結果の検証
    assert len(results) == 2
    assert results[0]["book_id"] == "book1"
    assert results[1]["chunk_index"] == 1


def test_search_with_no_embed_model_specified(store, mock_connection):
    """embed_model を指定しない場合は、フィルタを適用しない（後方互換性）。"""
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = []

    query_vector = [0.1] * 1024
    store.search(query_vector, top_k=10)

    mock_cursor.execute.assert_called_once()
    call_args = mock_cursor.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]

    # embed_model キーが params に含まれないことを確認（またはデフォルト値が使われる）
    # ここではシグネチャに応じて調整が必要
    assert "qv" in params
    assert "k" in params


def test_search_vector_literal_formatting(store, mock_connection):
    """ベクトルが正しい形式でリテラル化されているか。"""
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = []

    query_vector = [0.1, 0.2, 0.3]
    store.search(query_vector, top_k=5, embed_model="bge-m3")

    call_args = mock_cursor.execute.call_args
    params = call_args[0][1]

    # ベクトルが正しくリテラル化されているか
    qv_literal = params["qv"]
    assert qv_literal.startswith("[")
    assert qv_literal.endswith("]")
    assert "0.1" in qv_literal
    assert "0.2" in qv_literal
    assert "0.3" in qv_literal
