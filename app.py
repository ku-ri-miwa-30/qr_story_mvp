"""
app.py — アプリの入口（組み立て役）

■ フォルダ構成と「3層構造」
    リクエスト
       ↓
    routes/        ルート層      … リクエストを受けて、レスポンス（HTML / JSON）を返す
       ↓ 呼ぶ
    services/      サービス層    … 業務ルール（権限・状態遷移・入力チェック・トランザクション）
       ↓ 呼ぶ
    repositories/  リポジトリ層  … SQL で DB を読み書きする
       ↓
    SQLite（data/story_qr.db）

    上の層は下の層だけを呼ぶ（下から上は呼ばない）のがルール。
    Java/Spring の案件の Controller → Service → Repository と同じ考え方です。

■ 起動方法
    python app.py                      # 開発用サーバー（http://127.0.0.1:5000）
    gunicorn "app:create_app()"        # 本番（Render）
"""

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import db
from config import load_config
from routes import admin, api, auth, public
from services import auth_service, permissions, product_service, workflow_service


def create_app(test_config=None):
    """
    アプリを作って返す関数（「アプリケーションファクトリ」と呼ばれる書き方）。
    テストのときに別の設定（一時的なDBなど）でアプリを作れるのが利点。
    """
    app = Flask(__name__)
    app.config.update(load_config())
    if test_config:
        app.config.update(test_config)

    if app.config["APP_ENV"] == "production" and app.config["SECRET_KEY"] == "dev-secret-change-me":
        raise RuntimeError("本番環境では環境変数 SECRET_KEY を必ず設定してください。")

    # Render などのプロキシの後ろで動くとき、元の https や IP アドレスを正しく受け取る設定
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # JSON で日本語を \uXXXX にせず、そのまま返す
    app.json.ensure_ascii = False

    # ---- DB ----
    db.init_app(app)
    with app.app_context():
        auth_service.ensure_initial_users(app.config)

    # ---- ルート（ブループリント）を登録 ----
    auth.init_csrf(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)

    # ---- テンプレートから使える関数・値 ----
    app.jinja_env.globals.update(
        story_url=public.story_url,
        image_url=public.image_url,
        can=permissions.can,
        STATUS_LABELS=product_service.STATUS_LABELS,
        TRANSITIONS=workflow_service.TRANSITIONS,
        REQUEST_STATUS_LABELS=workflow_service.REQUEST_STATUS_LABELS,
        ROLE_LABELS=permissions.ROLE_LABELS,
    )

    @app.template_filter("jdate")
    def jdate(value):
        """'2026-04-15' → '2026年4月15日'"""
        try:
            y, m, d = str(value)[:10].split("-")
            return f"{int(y)}年{int(m)}月{int(d)}日"
        except (ValueError, TypeError):
            return value

    @app.context_processor
    def inject_counts():
        """ヘッダーの「承認待ち(3)」のような件数を、全画面で使えるようにする。"""
        from flask import g

        if getattr(g, "current_user", None) is None or request.path.startswith("/api/"):
            return {}
        return {"pending_count": product_service.status_counts().get("pending", 0)}

    # ---- エラー画面（API のときは JSON で返す）----
    def _error(status, code, message, template=None):
        if request.path.startswith("/api/"):
            return jsonify({"error": {"code": code, "message": message}}), status
        if template:
            return render_template(template), status
        return message, status

    @app.errorhandler(404)
    def not_found(e):
        return _error(404, "NOT_FOUND", "見つかりません。", "404.html")

    @app.errorhandler(405)
    def method_not_allowed(e):
        return _error(405, "METHOD_NOT_ALLOWED", "このURLではそのHTTPメソッドは使えません。")

    @app.errorhandler(413)
    def too_large(e):
        return _error(413, "PAYLOAD_TOO_LARGE", "ファイルが大きすぎます（5MBまで）。")

    @app.errorhandler(500)
    def server_error(e):
        return _error(500, "INTERNAL_ERROR", "サーバーでエラーが発生しました。")

    return app


if __name__ == "__main__":
    # debug=True は開発専用（エラー内容が画面に出る・保存で自動再起動）。本番では使わない
    create_app().run(host="0.0.0.0", port=5000, debug=True)
