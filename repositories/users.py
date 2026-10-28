"""users 表を読み書きする。"""

from db import get_db


def count():
    return get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]


def find_by_id(user_id):
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def find_by_username(username):
    return get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def find_by_token_hash(token_hash):
    return get_db().execute(
        "SELECT * FROM users WHERE api_token_hash = ?", (token_hash,)
    ).fetchone()


def insert(username, display_name, password_hash, role):
    cursor = get_db().execute(
        "INSERT INTO users (username, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
        (username, display_name, password_hash, role),
    )
    return cursor.lastrowid


def update_token_hash(user_id, token_hash):
    get_db().execute("UPDATE users SET api_token_hash = ? WHERE id = ?", (token_hash, user_id))
