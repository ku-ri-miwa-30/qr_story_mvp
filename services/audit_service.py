"""
audit_service.py — 監査ログを書く

監査ログの基本の「5W」：
    誰が（actor） いつ（occurred_at） 何に（target） 何をして（action）
    どう変わったか（before → after）
に加えて、どこから（via: 画面 or API、ip_address）を記録します。

大事なポイント：
    record() は、業務データの変更と「同じトランザクションの中」で呼ぶこと。
    そうすれば「データは変わったのにログが無い」という状態が起こらない。
"""

import json

from flask import g, has_request_context, request

from repositories import audit_logs as audit_repo
from services import permissions


def _to_json(value):
    if value is None:
        return None
    # ensure_ascii=False … 日本語を あ のような形にせず、そのまま保存する
    # default=str … 日付など JSON にできない値は文字列にする
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def record(actor, action, target_type=None, target_id=None, before=None, after=None,
           actor_name=None):
    """
    actor … 操作した人（users の行）。ログイン失敗などで誰か分からないときは None
    action … 'product.create' のような「対象.操作」の文字列
    """
    via, ip = None, None
    if has_request_context():
        via = g.get("via", "web")
        ip = request.remote_addr

    audit_repo.insert({
        "actor_user_id": actor["id"] if actor else None,
        "actor_name": actor["display_name"] if actor else actor_name,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before_json": _to_json(before),
        "after_json": _to_json(after),
        "via": via,
        "ip_address": ip,
    })


def diff(before, after):
    """
    変更があった項目だけを取り出す。
    全項目を毎回ログに残すと、何が変わったのか読み取りにくいため。
        before={'name': 'A', 'origin': 'X'}, after={'name': 'B', 'origin': 'X'}
        → ({'name': 'A'}, {'name': 'B'})
    """
    changed = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}


def to_dict(row):
    """API で返す形に変換（JSON文字列 → 辞書に戻す）。"""
    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "actor": {"user_id": row["actor_user_id"], "name": row["actor_name"]},
        "action": row["action"],
        "target": {"type": row["target_type"], "id": row["target_id"]},
        "before": json.loads(row["before_json"]) if row["before_json"] else None,
        "after": json.loads(row["after_json"]) if row["after_json"] else None,
        "via": row["via"],
        "ip_address": row["ip_address"],
    }


def search(user, **filters):
    permissions.require(user, "audit.read")
    return audit_repo.search(**filters)
