"""
db.py — データベース（SQLite）との接続と「トランザクション」

■ トランザクションとは？
    「複数のDB操作を、全部成功 or 全部なかったことにする」ためのまとまり。
    例：承認ボタンを押したとき
        ① 商品のステータスを「公開中」に変える
        ② 申請を「承認済み」にする
        ③ 監査ログに「誰がいつ承認したか」を書く
    ③だけ失敗して①②が残ると「誰が承認したか分からない公開商品」ができてしまう。
    なので①〜③を1つのトランザクションにまとめ、途中で失敗したら全部取り消す（ロールバック）。

    このアプリでは「サービス層」（services/）が transaction() を使い、
    「リポジトリ層」（repositories/）は commit しない、というルールにしています。
"""

import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # row["name"] のように列名で取り出せるようにする
    conn.execute("PRAGMA foreign_keys = ON")  # 外部キー制約を有効化（SQLiteは初期状態で無効）
    return conn


def get_db():
    """今のリクエスト用のDB接続を返す（1リクエストにつき1接続を使い回す）。"""
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE"])
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction():
    """
    使い方：
        with transaction():
            products_repo.update_status(...)
            audit_service.record(...)
        # ← with を抜けた時点で commit。途中で例外が起きたら rollback。

    with の中でさらに transaction() を使っても大丈夫なように、
    「一番外側の with だけが commit / rollback する」作りにしている。
    （例：JSONインポートは「商品登録」を何件も呼ぶが、全体で1トランザクションにしたい）
    """
    db = get_db()
    depth = g.get("tx_depth", 0)
    g.tx_depth = depth + 1
    try:
        yield db
        if depth == 0:
            db.commit()
    except Exception:
        if depth == 0:
            db.rollback()
        raise
    finally:
        g.tx_depth = depth


def init_db(app):
    """schema.sql を実行して表を作る（IF NOT EXISTS なので既存データは消えない）。"""
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    conn = _connect(app.config["DATABASE"])
    # WAL モード：読み込みと書き込みが同時に来ても待たされにくくなる設定
    conn.execute("PRAGMA journal_mode = WAL")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    init_db(app)
