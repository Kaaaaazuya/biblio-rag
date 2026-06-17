"""WebUI E2E: ブラウザから PDF を presigned URL で MinIO に直接アップロードする。

実ブラウザ(Playwright/chromium)と MinIO が要る。未準備なら skip。
事前準備（初回のみ）: uv run playwright install chromium
実行: uv run pytest -m webui

隔離: 一意なファイル名でアップロードし、teardown で MinIO オブジェクトと
サイドカー JSON を削除する（実データを汚さない）。
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from workers import config
from workers.storage import ObjectStore

pytestmark = pytest.mark.webui

FIXTURE = Path(__file__).parent / "fixtures" / "sample_book.pdf"


def _minio_up() -> bool:
    try:
        ObjectStore().list_keys()
        return True
    except Exception:
        return False


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    """webui.server を uvicorn サブプロセスで起動し、起動を待ってから URL を返す。"""
    if not _minio_up():
        pytest.skip("MinIO 未起動（docker compose up が必要）")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "webui.server:app", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/", timeout=1.0).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            pytest.fail("uvicorn が起動しませんでした")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def test_webui_upload_to_minio(page, server, tmp_path):
    # 一意名でアップロード（既存の raw/ を壊さない）
    name = f"e2e_webui_{uuid.uuid4().hex[:8]}.pdf"
    pdf = tmp_path / name
    shutil.copy(FIXTURE, pdf)
    key = f"raw/{name}"
    meta = Path("books") / f"{pdf.stem}.meta.json"

    try:
        page.goto(server)
        page.set_input_files("#file", str(pdf))
        page.fill("#title", "E2E WebUI サンプル")
        page.fill("#author", "テスト著者")
        page.click("#submit")

        # 成功すると #status に class "ok" と「完了」が付く
        page.locator("#status.ok").wait_for(timeout=20000)
        assert "完了" in page.inner_text("#status")

        # MinIO に着地している
        assert key in ObjectStore().list_keys(), f"{key} が MinIO に無い"
        # メタデータも保存されている
        assert meta.exists(), f"{meta} が保存されていない"
    finally:
        with contextlib.suppress(Exception):
            config.s3_client().delete_object(Bucket=config.S3_BUCKET, Key=key)
        meta.unlink(missing_ok=True)
