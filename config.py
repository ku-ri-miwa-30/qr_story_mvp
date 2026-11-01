"""
config.py — 設定を1か所にまとめるファイル

設定値は「環境変数」から読み込みます。
    ・パスワードや秘密鍵をコードに直書きしない（GitHubに上げても安全）
    ・同じコードのまま、手元のPCとRender（本番）で設定だけ変えられる
という、実務でもほぼ必ず使われる考え方です（「Twelve-Factor App」と呼ばれる設計指針の1つ）。
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """環境変数を読んで、設定の辞書を返す。create_app() から呼ばれる。"""
    app_env = os.environ.get("APP_ENV", "development")  # development / production

    # データ（DBファイル・アップロード画像）を置くフォルダ。
    # ※ Render の無料プランではこのフォルダは「一時的」で、再起動・再デプロイ・
    #   15分アクセスなしのスリープのたびに中身が消える（README の「Render に公開する」参照）。
    #   有料プランで永続ディスクを付けたら、そのマウント先（例：/var/data）を指定する。
    data_dir = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))

    return {
        "APP_ENV": app_env,
        "DATA_DIR": data_dir,
        "DATABASE": os.path.join(data_dir, "story_qr.db"),
        "UPLOAD_FOLDER": os.path.join(data_dir, "uploads"),
        # session（ログイン状態）の改ざんを防ぐための秘密鍵
        "SECRET_KEY": os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        # QRに埋め込むURLの先頭。空なら「今アクセスしているURL」から自動で作る
        "BASE_URL": os.environ.get("BASE_URL", "").rstrip("/"),
        # アップロードの上限（5MB）。超えると 413 エラー
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
        # 初回起動時に自動で作るユーザーのパスワード（本番では必ず環境変数で指定）
        "INITIAL_EDITOR_PASSWORD": os.environ.get("EDITOR_PASSWORD", "editor"),
        "INITIAL_APPROVER_PASSWORD": os.environ.get("APPROVER_PASSWORD", "approver"),
        # true にすると、DB が空のとき起動時にサンプル商品を自動で入れる（無料プランのデモ用）
        "SEED_SAMPLE_DATA": os.environ.get("SEED_SAMPLE_DATA", "false").lower() == "true",
        # Cookie の安全設定
        "SESSION_COOKIE_HTTPONLY": True,  # JavaScript から Cookie を読めなくする
        "SESSION_COOKIE_SAMESITE": "Lax",  # 他サイトからの送信で Cookie を付けにくくする
        "SESSION_COOKIE_SECURE": app_env == "production",  # 本番は HTTPS のときだけ送る
    }
