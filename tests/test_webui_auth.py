"""WebUI 認証ミドルウェアのテスト。"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from webui.auth import AuthMiddleware


def _create_app_with_auth():
    """テスト用の最小限なアプリケーション（静的ファイル相当の '/' も含む）。"""

    def hello(request):
        return JSONResponse({"hello": "world"})

    app = Starlette(
        routes=[
            Route("/api/health", hello, methods=["GET"]),
            Route("/api/protected", hello, methods=["GET"]),
            Route("/", hello, methods=["GET"]),
        ]
    )
    app.add_middleware(AuthMiddleware)
    return app


def _mock_config(
    auth_enabled=False, auth_method="token", auth_token="", auth_username="", auth_password=""
):
    """config モジュック用のモック。"""
    mock_cfg = MagicMock()
    mock_cfg.WEBUI_AUTH_ENABLED = auth_enabled
    mock_cfg.WEBUI_AUTH_METHOD = auth_method
    mock_cfg.WEBUI_AUTH_TOKEN = auth_token
    mock_cfg.WEBUI_AUTH_USERNAME = auth_username
    mock_cfg.WEBUI_AUTH_PASSWORD = auth_password
    return mock_cfg


def test_auth_disabled_by_default():
    """デフォルトでは認証が無効。"""
    with patch("webui.auth.config", _mock_config()):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected")
        assert res.status_code == 200


def test_health_always_accessible():
    """ヘルスチェックエンドポイントは認証なしでアクセス可能。"""
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret")):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/health")
        assert res.status_code == 200


def test_token_auth_enabled():
    """トークン認証有効時、有効なトークンでアクセス可能。"""
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret-token-123")):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected", headers={"Authorization": "Bearer secret-token-123"})
        assert res.status_code == 200


def test_token_auth_rejects_invalid_token():
    """トークン認証有効時、無効なトークンで拒否。"""
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret-token-123")):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected", headers={"Authorization": "Bearer wrong-token"})
        assert res.status_code == 401
        assert res.json()["detail"] == "Unauthorized"


def test_token_auth_rejects_missing_auth():
    """トークン認証有効時、認証ヘッダなしで拒否。"""
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret-token-123")):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected")
        assert res.status_code == 401
        assert res.json()["detail"] == "Unauthorized"


def test_basic_auth_enabled():
    """Basic認証有効時、有効な認証情報でアクセス可能。"""
    mock_cfg = _mock_config(
        auth_enabled=True, auth_method="basic", auth_username="user", auth_password="pass"
    )
    with patch("webui.auth.config", mock_cfg):
        auth_str = base64.b64encode(b"user:pass").decode("utf-8")
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected", headers={"Authorization": f"Basic {auth_str}"})
        assert res.status_code == 200


def test_basic_auth_rejects_invalid_password():
    """Basic認証有効時、パスワードが違うと拒否。"""
    mock_cfg = _mock_config(
        auth_enabled=True, auth_method="basic", auth_username="user", auth_password="pass"
    )
    with patch("webui.auth.config", mock_cfg):
        auth_str = base64.b64encode(b"user:wrong").decode("utf-8")
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected", headers={"Authorization": f"Basic {auth_str}"})
        assert res.status_code == 401


def test_basic_auth_rejects_missing_auth():
    """Basic認証有効時、認証ヘッダなしで拒否。"""
    mock_cfg = _mock_config(
        auth_enabled=True, auth_method="basic", auth_username="user", auth_password="pass"
    )
    with patch("webui.auth.config", mock_cfg):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected")
        assert res.status_code == 401


def test_basic_auth_rejects_malformed_header():
    """Basic認証ヘッダが不正な形式で拒否。"""
    mock_cfg = _mock_config(
        auth_enabled=True, auth_method="basic", auth_username="user", auth_password="pass"
    )
    with patch("webui.auth.config", mock_cfg):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/api/protected", headers={"Authorization": "Basic !!!"})
        assert res.status_code == 401


def test_token_auth_allows_static_files_without_credentials():
    """トークン認証有効時、静的ファイル（/api/ 以外）は認証なしでアクセス可能。

    ブラウザは初回ページロード時に Authorization ヘッダを自動送信しないため、
    token 認証は API のみを保護対象とする。
    """
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret")):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200


def test_basic_auth_protects_static_files():
    """Basic認証有効時は、静的ファイルも含めて保護対象（ブラウザが認証ダイアログを出せるため）。"""
    mock_cfg = _mock_config(
        auth_enabled=True, auth_method="basic", auth_username="user", auth_password="pass"
    )
    with patch("webui.auth.config", mock_cfg):
        app = _create_app_with_auth()
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 401


def test_health_check_path_boundary_not_bypassed():
    """/api/health_check のような類似パスは保護対象のまま（前方一致誤爆の防止）。"""
    with patch("webui.auth.config", _mock_config(auth_enabled=True, auth_token="secret")):
        from starlette.applications import Starlette as _Starlette
        from starlette.responses import JSONResponse as _JSONResponse
        from starlette.routing import Route as _Route

        def hello(request):
            return _JSONResponse({"hello": "world"})

        app = _Starlette(routes=[_Route("/api/health_check", hello, methods=["GET"])])
        app.add_middleware(AuthMiddleware)
        client = TestClient(app)
        res = client.get("/api/health_check")
        assert res.status_code == 401
