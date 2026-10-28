"""approval_requests 表（申請）を読み書きする。"""

from db import get_db


def insert(product_id, requested_by, comment, snapshot_json):
    cursor = get_db().execute(
        """
        INSERT INTO approval_requests (product_id, requested_by, request_comment, snapshot_json)
        VALUES (?, ?, ?, ?)
        """,
        (product_id, requested_by, comment, snapshot_json),
    )
    return cursor.lastrowid


def find_open_by_product(product_id):
    """その商品の「まだ結論が出ていない申請」（status = pending）を1件取得。"""
    return get_db().execute(
        "SELECT * FROM approval_requests WHERE product_id = ? AND status = 'pending' ORDER BY id DESC",
        (product_id,),
    ).fetchone()


def close(request_id, status, reviewed_by, comment):
    """申請に結論（approved / rejected / withdrawn）を書き込む。"""
    get_db().execute(
        """
        UPDATE approval_requests
        SET status = ?, reviewed_by = ?, review_comment = ?,
            reviewed_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (status, reviewed_by, comment, request_id),
    )


# 申請者・承認者の名前も一緒に取りたいので users 表を2回 JOIN している
# （同じ表を2回使うときは AS で別名を付けて区別する）
_SELECT_WITH_NAMES = """
    SELECT a.*, p.name AS product_name,
           ru.display_name AS requested_by_name,
           vu.display_name AS reviewed_by_name
    FROM approval_requests AS a
    JOIN products AS p ON p.id = a.product_id
    JOIN users AS ru ON ru.id = a.requested_by
    LEFT JOIN users AS vu ON vu.id = a.reviewed_by
"""


def find_by_product(product_id):
    return get_db().execute(
        _SELECT_WITH_NAMES + " WHERE a.product_id = ? ORDER BY a.id DESC", (product_id,)
    ).fetchall()


def find_all(status=None):
    sql, params = _SELECT_WITH_NAMES, []
    if status:
        sql += " WHERE a.status = ?"
        params.append(status)
    sql += " ORDER BY a.id DESC LIMIT 200"
    return get_db().execute(sql, params).fetchall()
