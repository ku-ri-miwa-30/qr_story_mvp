"""
auth.py — ログイン／ログアウトと、画面のセキュリティ対策（CSRF）
"""

import secrets
from functools import wraps

from flask import (Blueprint, flash, g, redirect, render_template, request, session,
                   url_for)

from services import auth_service

bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------
# リクエストのたびに「今ログインしている人」を g.current_user に入れる
# ---------------------------------------------------------------------
@bp.before_app_request
def load_current_user():
    g.via = "web"
    user_id = session.get("user_id")
    g.current_user = auth_service.find_user(user_id) if user_id else None


def login_required(view_func):
    """ルート関数の上に @login_required と書くと、ログインしている人だけが見られる。"""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if g.current_user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------
# CSRF（シーサーフ）対策
# ---------------------------------------------------------------------
# CSRF攻撃とは：ログイン中の人が悪意あるサイトを開くと、そのサイトから
#   こっそり「商品を削除」フォームを送信させられてしまう攻撃。
# 対策：フォームに「このサイトが発行した合言葉（トークン）」を隠して入れておき、
#   POST のたびに合言葉が正しいか確認する。悪意あるサイトは合言葉を知らないので送れない。
#
# ※ API（/api/...）は Cookie ではなく APIトークンで認証するので、CSRF の心配がなく対象外。
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def init_csrf(app):
    # テンプレートの中で {{ csrf_token() }} と書けるようにする
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method != "POST" or request.path.startswith("/api/"):
            return None
        sent = request.form.get("csrf_token", "")
        expected = session.get("_csrf", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            flash("画面の有効期限が切れました。もう一度操作してください。", "error")
            return redirect(request.referrer or url_for("auth.login"))
        return None


# ---------------------------------------------------------------------
# ログイン / ログアウト
# ---------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = auth_service.authenticate(
            request.form.get("username", "").strip(), request.form.get("password", "")
        )
        if user:
            # ログインの瞬間に session を作り直す（セッション固定攻撃の対策）
            session.clear()
            session["user_id"] = user["id"]
            flash(f"{user['display_name']}としてログインしました。", "success")
            next_url = request.args.get("next", "")
            # 自サイト内の URL だけ許可（外部サイトへ飛ばされる攻撃を防ぐ）
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("admin.product_list")
            return redirect(next_url)
        flash("ユーザー名またはパスワードが違います。", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("ログアウトしました。", "success")
    return redirect(url_for("auth.login"))
