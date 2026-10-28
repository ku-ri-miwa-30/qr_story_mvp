"""
audit_logs 表（監査ログ）を読み書きする。

※ わざと insert と search しか用意していない。
  「監査ログは後から書き換え・削除できない」ことを、コードの形でも保証するため。
"""

from db import get_db


def insert(entry):
    get_db().execute(
        """
        INSERT INTO audit_logs
            (actor_user_id, actor_name, action, target_type, target_id,
             before_json, after_json, via, ip_address)
        VALUES
            (:actor_user_id, :actor_name, :action, :target_type, :target_id,
             :before_json, :after_json, :via, :ip_address)
        """,
        entry,
    )


def search(action=None, target_type=None, target_id=None, actor=None, limit=100):
    """
    条件を「指定されたものだけ」WHERE に足していく書き方。
    検索画面でよく使うパターンなので覚えておくと便利。
    """
    conditions, params = [], []
    if action:
        # LIKE 'product.%' のように前方一致で探せるようにする
        conditions.append("action LIKE ?")
        params.append(f"{action}%")
    if target_type:
        conditions.append("target_type = ?")
        params.append(target_type)
    if target_id is not None:
        conditions.append("target_id = ?")
        params.append(target_id)
    if actor:
        conditions.append("actor_name = ?")
        params.append(actor)

    sql = "SELECT * FROM audit_logs"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return get_db().execute(sql, params).fetchall()
