"""チャット生成層のインターフェース契約。

開発/本番で実装を差し替えるための抽象。ストリーミング生成に対応。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ChatClient(ABC):
    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """チャットメッセージをストリーミング生成する。各トークンを yield する。

        非同期ジェネレータの契約。実装は ``async def`` + ``yield`` で提供する
        （呼び出し側は ``async for`` で消費する）。

        Args:
            messages: role/content を持つメッセージ辞書のリスト。
            model: 使用するモデル。指定時は実装ごとのデフォルトをオーバーライド。

        Yields:
            生成されたトークン（テキスト片）。
        """
        ...
