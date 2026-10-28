"""products / timeline_events / scans 表を読み書きする。"""

from db import get_db

# 画面やAPIから書き換えてよい列（それ以外の列は勝手に書き換えさせない）
EDITABLE_COLUMNS = ("name", "producer_name", "origin", "catchcopy", "story", "producer_message")


# ---------------------------------------------------------------------
# products
# ---------------------------------------------------------------------
def find_all(status=None):
    """商品一覧（読み取り回数つき）。status を渡すとその状態だけに絞る。"""
    sql = """
        SELECT p.*, COUNT(s.id) AS scan_count
        FROM products AS p
        LEFT JOIN scans AS s ON s.product_id = p.id
    """
    params = []
    if status:
        sql += " WHERE p.status = ?"
        params.append(status)
    sql += " GROUP BY p.id ORDER BY p.updated_at DESC, p.id DESC"
    return get_db().execute(sql, params).fetchall()


def find_by_id(product_id):
    return get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def find_by_slug(slug):
    return get_db().execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()


def slug_exists(slug):
    return get_db().execute("SELECT 1 FROM products WHERE slug = ?", (slug,)).fetchone() is not None


def insert(data, slug, created_by, image_filename=None):
    cursor = get_db().execute(
        """
        INSERT INTO products
            (slug, name, producer_name, origin, catchcopy, story, producer_message,
             image_filename, created_by)
        VALUES
            (:slug, :name, :producer_name, :origin, :catchcopy, :story, :producer_message,
             :image_filename, :created_by)
        """,
        {**data, "slug": slug, "image_filename": image_filename, "created_by": created_by},
    )
    return cursor.lastrowid


def update(product_id, data, expected_version, image_filename):
    """
    楽観的ロック付きの更新。
    WHERE に「version = 画面を開いたときのバージョン」を入れておくと、
    その間に誰かが更新していた場合は version が変わっているので 0 行更新になる。
    戻り値（更新できた行数）が 0 なら「先に誰かが更新した」と判断できる。
    """
    cursor = get_db().execute(
        """
        UPDATE products SET
            name = :name, producer_name = :producer_name, origin = :origin,
            catchcopy = :catchcopy, story = :story, producer_message = :producer_message,
            image_filename = :image_filename,
            version = version + 1,
            updated_at = datetime('now', 'localtime')
        WHERE id = :id AND version = :version
        """,
        {**data, "image_filename": image_filename, "id": product_id, "version": expected_version},
    )
    return cursor.rowcount


def update_status(product_id, new_status):
    get_db().execute(
        """
        UPDATE products
        SET status = ?, version = version + 1, updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (new_status, product_id),
    )


def touch(product_id):
    """年表の追加・削除も「商品の変更」とみなして version を上げる。"""
    get_db().execute(
        "UPDATE products SET version = version + 1, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (product_id,),
    )


def delete(product_id):
    get_db().execute("DELETE FROM products WHERE id = ?", (product_id,))


def count_by_status():
    rows = get_db().execute("SELECT status, COUNT(*) AS cnt FROM products GROUP BY status").fetchall()
    return {row["status"]: row["cnt"] for row in rows}


# ---------------------------------------------------------------------
# timeline_events
# ---------------------------------------------------------------------
def find_events(product_id):
    return get_db().execute(
        "SELECT * FROM timeline_events WHERE product_id = ? ORDER BY event_date, id",
        (product_id,),
    ).fetchall()


def find_event(event_id):
    return get_db().execute("SELECT * FROM timeline_events WHERE id = ?", (event_id,)).fetchone()


def insert_event(product_id, event):
    cursor = get_db().execute(
        "INSERT INTO timeline_events (product_id, event_date, title, description) VALUES (?, ?, ?, ?)",
        (product_id, event["event_date"], event["title"], event["description"]),
    )
    return cursor.lastrowid


def delete_event(event_id):
    get_db().execute("DELETE FROM timeline_events WHERE id = ?", (event_id,))


# ---------------------------------------------------------------------
# scans
# ---------------------------------------------------------------------
def insert_scan(product_id, user_agent):
    get_db().execute(
        "INSERT INTO scans (product_id, user_agent) VALUES (?, ?)", (product_id, user_agent)
    )


def count_scans(product_id):
    return get_db().execute(
        "SELECT COUNT(*) FROM scans WHERE product_id = ?", (product_id,)
    ).fetchone()[0]


def daily_scans(product_id, days=7):
    """直近 days 日間の {'2026-10-27': 5, ...}"""
    rows = get_db().execute(
        """
        SELECT date(scanned_at) AS day, COUNT(*) AS cnt
        FROM scans
        WHERE product_id = ? AND scanned_at >= date('now', 'localtime', ?)
        GROUP BY day
        """,
        (product_id, f"-{days - 1} days"),
    ).fetchall()
    return {row["day"]: row["cnt"] for row in rows}
