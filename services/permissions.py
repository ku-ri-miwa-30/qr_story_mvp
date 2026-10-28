"""
permissions.py — 権限（ロールごとに「できること」）の定義

「ロール → 権限の一覧」を表（辞書）で1か所にまとめておくと、
    ・どの役割が何をできるか一目で分かる（設計書にもそのまま書ける）
    ・権限を変えたいときは、この表を直すだけで済む
という利点があります。この考え方を RBAC（ロールベースアクセス制御）と呼びます。
"""

from errors import ForbiddenError

# 権限の名前は「対象.操作」の形にそろえる
ROLE_PERMISSIONS = {
    "editor": {
        "product.write",  # 登録・編集・年表の追加削除
        "product.submit",  # 承認申請
        "product.withdraw",  # 申請の取り下げ
        "transfer.import",  # JSONファイルの取り込み
    },
    "approver": {
        "product.write",
        "product.submit",
        "product.withdraw",
        "transfer.import",
        "product.approve",  # 承認
        "product.reject",  # 差し戻し
        "product.unpublish",  # 公開停止
        "product.delete",  # 削除
        "audit.read",  # 監査ログの閲覧
    },
}

ROLE_LABELS = {"editor": "編集者", "approver": "承認者"}


def can(user, permission):
    """user がその権限を持っていれば True。テンプレートからも使う。"""
    if user is None:
        return False
    return permission in ROLE_PERMISSIONS.get(user["role"], set())


def require(user, permission):
    """権限がなければ ForbiddenError を投げる。サービスの関数の先頭で呼ぶ。"""
    if not can(user, permission):
        raise ForbiddenError("この操作を行う権限がありません。")
