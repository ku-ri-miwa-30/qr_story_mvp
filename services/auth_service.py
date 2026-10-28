"""
auth_service.py — ログイン（認証）と APIトークン

■ 認証（Authentication）と 認可（Authorization）の違い
    認証 … 「あなたは誰？」を確かめる（ログイン、APIトークン）   ← このファイル
    認可 … 「あなたはそれをしていい？」を確かめる（ロールと権限） ← permissions.py

■ 画面とAPIで認証方法を分けている理由
    画面 … ログインすると Cookie（session）で状態を覚える
    API  … リクエストごとに「Authorization: Bearer <トークン>」ヘッダーを付けてもらう
    システム同士の連携（バッチや他システムからの呼び出し）には Cookie が使いにくいので、
    API はトークン方式にするのが一般的。
"""

import hashlib
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from db import transaction
from repositories import users as users_repo
from services import audit_service


def _hash_token(raw_token):
    # トークンは十分に長いランダム値なので、SHA-256 で1回ハッシュするだけで安全に保存できる
    return hashlib.sha256(raw_token.encode()).hexdigest()


def ensure_initial_users(config):
    """ユーザーが1人もいなければ、編集者と承認者を1人ずつ作る（初回起動時だけ）。"""
    if users_repo.count() > 0:
        return
    if config["APP_ENV"] == "production" and (
        config["INITIAL_EDITOR_PASSWORD"] == "editor" or config["INITIAL_APPROVER_PASSWORD"] == "approver"
    ):
        raise RuntimeError("本番環境では EDITOR_PASSWORD と APPROVER_PASSWORD を環境変数で設定してください。")
    with transaction():
        users_repo.insert("editor", "編集者（生産者）",
                          generate_password_hash(config["INITIAL_EDITOR_PASSWORD"]), "editor")
        users_repo.insert("approver", "承認者（運営）",
                          generate_password_hash(config["INITIAL_APPROVER_PASSWORD"]), "approver")


def authenticate(username, password):
    """
    ユーザー名とパスワードを確認し、正しければ users の行を返す。違えば None。
    成功・失敗どちらも監査ログに残す（不正ログインの試みに気づけるように）。
    """
    user = users_repo.find_by_username(username)
    ok = user is not None and check_password_hash(user["password_hash"], password)
    with transaction():
        if ok:
            audit_service.record(user, "auth.login", "user", user["id"])
        else:
            # 失敗時は「誰が試したか」が分からないので、入力されたユーザー名を残す
            audit_service.record(None, "auth.login_failed", "user", None,
                                 after={"username": username[:50]},
                                 actor_name=f"(未認証) {username[:50]}")
    return user if ok else None


def find_user(user_id):
    return users_repo.find_by_id(user_id)


def issue_api_token(user):
    """
    APIトークンを新しく発行して「生の値」を返す（画面に1回だけ表示する）。
    DB にはハッシュ値しか保存しないので、二度と表示できない。古いトークンは無効になる。
    """
    raw = "sqr_" + secrets.token_urlsafe(32)
    with transaction():
        users_repo.update_token_hash(user["id"], _hash_token(raw))
        audit_service.record(user, "auth.issue_token", "user", user["id"])
    return raw


def user_from_token(raw_token):
    if not raw_token:
        return None
    return users_repo.find_by_token_hash(_hash_token(raw_token))
