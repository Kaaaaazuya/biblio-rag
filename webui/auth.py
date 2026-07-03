"""WebUI アクセス制御ミドルウェア。トークン認証または Basic 認証で保護。"""

from __future__ import annotations

import base64
import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from workers import config

logger = logging.getLogger(__name__)

# 認証不要なエンドポイント（ヘルスチェックは常に実行可能）
UNPROTECTED_PATHS = {"/api/health", "/"}


def _extract_bearer_token(auth_header: str | None) -> str | None:
    """Authorization: Bearer <token> から トークンを抽出。"""
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _extract_basic_auth(auth_header: str | None) -> tuple[str, str] | None:
    """Authorization: Basic <base64> から ユーザー名・パスワードを抽出。"""
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
        return (username, password)
    except ValueError, UnicodeDecodeError:
        return None


def _verify_token_auth(token: str) -> bool:
    """トークン認証で受け取ったトークンを検証。"""
    return config.WEBUI_AUTH_TOKEN and token == config.WEBUI_AUTH_TOKEN


def _verify_basic_auth(username: str, password: str) -> bool:
    """Basic認証で受け取ったユーザー名・パスワードを検証。"""
    return (
        config.WEBUI_AUTH_USERNAME
        and config.WEBUI_AUTH_PASSWORD
        and username == config.WEBUI_AUTH_USERNAME
        and password == config.WEBUI_AUTH_PASSWORD
    )


def _is_protected_path(path: str) -> bool:
    """パスが認証保護対象かを判定。"""
    for unprotected in UNPROTECTED_PATHS:
        if path == unprotected or path.startswith(unprotected):
            return False
    return True


class AuthMiddleware:
    """WebUI アクセス制御ミドルウェア。

    WEBUI_AUTH_ENABLED=true 時に有効。
    WEBUI_AUTH_METHOD=token で Bearer トークン認証。
    WEBUI_AUTH_METHOD=basic で HTTP Basic 認証。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 認証が無効か、保護対象でないパスはスキップ
        if not config.WEBUI_AUTH_ENABLED or not _is_protected_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        # Authorization ヘッダを取得
        headers = {name.decode(): value.decode() for name, value in scope.get("headers", [])}
        auth_header = headers.get("authorization")

        # 認証方式に応じて検証
        is_authenticated = False
        if config.WEBUI_AUTH_METHOD == "token":
            token = _extract_bearer_token(auth_header)
            is_authenticated = token is not None and _verify_token_auth(token)
        elif config.WEBUI_AUTH_METHOD == "basic":
            basic_auth = _extract_basic_auth(auth_header)
            if basic_auth:
                is_authenticated = _verify_basic_auth(*basic_auth)

        if not is_authenticated:
            error_response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
            await error_response(scope, receive, send)
            return

        await self.app(scope, receive, send)
