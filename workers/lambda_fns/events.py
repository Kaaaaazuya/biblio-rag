"""Lambda イベントから S3 の (bucket, key) を取り出す。

取り込みは「プレフィックスごとの S3 イベント → SQS → Lambda」で駆動する（ADR 0011）。
そのため Lambda が受け取るのは SQS イベントで、各レコードの body に S3 通知 JSON が入る。
テストや直接 S3 トリガーのために、S3 通知が直接来るケースも許容する。
"""

from __future__ import annotations

import json
from urllib.parse import unquote_plus


def s3_keys_from_event(event: dict) -> list[tuple[str, str]]:
    """イベント中のすべての S3 オブジェクトを (bucket, key) のリストで返す。"""
    pairs: list[tuple[str, str]] = []
    for record in event.get("Records", []):
        if "body" in record:  # SQS ラップ: body に S3 通知 JSON
            pairs.extend(_from_s3_notification(json.loads(record["body"])))
        elif "s3" in record:  # S3 が直接トリガー
            pairs.extend(_from_s3_notification({"Records": [record]}))
    return pairs


def _from_s3_notification(notification: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for r in notification.get("Records", []):
        s3 = r.get("s3")
        if not s3:
            continue
        bucket = s3["bucket"]["name"]
        key = unquote_plus(s3["object"]["key"])  # 通知はキーを URL エンコードする
        out.append((bucket, key))
    return out
