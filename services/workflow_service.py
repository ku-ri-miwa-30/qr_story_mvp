"""
workflow_service.py — 申請・承認フロー（ワークフロー）

■ 状態遷移表
    「今の状態」と「操作」の組み合わせで「次の状態」を決める表です。
    申請・承認システムの設計では、まずこの表（と状態遷移図）を作るのが定番です。
    docs/design.md に同じ内容を図にしています。

        操作        できる状態(from)      次の状態(to)   必要な権限
        submit      draft / rejected  →  pending        product.submit    申請する
        withdraw    pending           →  draft          product.withdraw  申請を取り下げる
        approve     pending           →  published      product.approve   承認する
        reject      pending           →  rejected       product.reject    差し戻す
        unpublish   published         →  draft          product.unpublish 公開を停止する

    表に無い組み合わせ（例：公開中の商品を approve）は、すべてエラー（409）にする。
    if 文を何個も書くより、表で持つ方が「抜け漏れ」に気づきやすく、変更にも強い。

■ 職務分掌（しょくむぶんしょう）
    「申請した本人は、その申請を承認・差し戻しできない」というルール。
    1人で申請も承認もできてしまうと、チェックの意味がなくなるため。
    監査（内部統制）の観点でとても大事なので、実務の承認システムでもほぼ必ず入る。
"""

import json

from db import transaction
from errors import ConflictError, ForbiddenError, ValidationError
from repositories import approvals as approvals_repo
from repositories import products as products_repo
from services import audit_service, permissions, product_service

TRANSITIONS = {
    "submit": {"from": {"draft", "rejected"}, "to": "pending",
               "permission": "product.submit", "label": "承認申請"},
    "withdraw": {"from": {"pending"}, "to": "draft",
                 "permission": "product.withdraw", "label": "申請の取り下げ"},
    "approve": {"from": {"pending"}, "to": "published",
                "permission": "product.approve", "label": "承認"},
    "reject": {"from": {"pending"}, "to": "rejected",
               "permission": "product.reject", "label": "差し戻し"},
    "unpublish": {"from": {"published"}, "to": "draft",
                  "permission": "product.unpublish", "label": "公開停止"},
}

REQUEST_STATUS_LABELS = {
    "pending": "審査中",
    "approved": "承認",
    "rejected": "差し戻し",
    "withdrawn": "取り下げ",
}


def available_actions(user, product):
    """
    今のユーザーが、この商品に対して実行できる操作の一覧。
    画面で「どのボタンを出すか」を決めるのに使う（出せないボタンは最初から出さない）。
    """
    actions = []
    open_request = None
    for action, rule in TRANSITIONS.items():
        if product["status"] not in rule["from"] or not permissions.can(user, rule["permission"]):
            continue
        if action in ("approve", "reject"):
            open_request = open_request or approvals_repo.find_open_by_product(product["id"])
            if open_request and open_request["requested_by"] == user["id"]:
                continue  # 自分の申請は承認・差し戻しできない
        actions.append(action)
    return actions


def perform(user, product_id, action, comment=""):
    """
    ワークフローの操作を1つ実行する。画面からも API からもこの関数を呼ぶ。
    戻り値：操作後の状態（'published' など）
    """
    # ① その操作は存在するか
    rule = TRANSITIONS.get(action)
    if rule is None:
        raise ValidationError(f"不明な操作です: {action}")

    # ② 権限はあるか
    permissions.require(user, rule["permission"])

    comment = (comment or "").strip()
    if len(comment) > 1000:
        raise ValidationError("コメントは1000文字以内にしてください。")

    product = product_service.get_product(product_id)
    old_status = product["status"]

    # ③ 今の状態からその操作ができるか（状態遷移表で判定）
    if old_status not in rule["from"]:
        raise ConflictError(
            f"「{product_service.STATUS_LABELS[old_status]}」の商品に対して"
            f"「{rule['label']}」はできません。"
        )

    with transaction():
        if action == "submit":
            # 申請時のルール：お客さんに見せる物語が空のままでは申請できない
            if not (product["story"] or "").strip():
                raise ValidationError("物語の本文を書いてから申請してください。",
                                      [{"field": "story", "message": "申請には物語の本文が必要です。"}])
            # 申請した時点の内容をスナップショットとして保存
            snapshot = product_service.to_dict(product, product_service.get_events(product_id))
            approvals_repo.insert(product_id, user["id"], comment,
                                  json.dumps(snapshot, ensure_ascii=False))

        elif action in ("approve", "reject", "withdraw"):
            request_row = approvals_repo.find_open_by_product(product_id)
            if request_row is None:
                raise ConflictError("審査中の申請が見つかりません。")

            if action in ("approve", "reject") and request_row["requested_by"] == user["id"]:
                raise ForbiddenError("自分が出した申請は、承認・差し戻しできません（職務分掌）。"
                                     "別の承認者に依頼してください。")
            if action == "reject" and not comment:
                raise ValidationError("差し戻しの理由を入力してください。",
                                      [{"field": "comment", "message": "差し戻し理由は必須です。"}])

            result = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
            approvals_repo.close(request_row["id"], result, user["id"], comment)

        # 商品の状態を変える（unpublish はここだけ）
        products_repo.update_status(product_id, rule["to"])

        audit_service.record(
            user, f"product.{action}", "product", product_id,
            before={"status": old_status},
            after={"status": rule["to"], "comment": comment or None},
        )

    return rule["to"]


def history(product_id):
    return approvals_repo.find_by_product(product_id)


def list_requests(status=None):
    if status and status not in REQUEST_STATUS_LABELS:
        raise ValidationError(f"status は {', '.join(REQUEST_STATUS_LABELS)} のいずれかで指定してください。")
    return approvals_repo.find_all(status)


def request_to_dict(row):
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "status": row["status"],
        "requested_by": row["requested_by_name"],
        "requested_at": row["requested_at"],
        "request_comment": row["request_comment"],
        "reviewed_by": row["reviewed_by_name"],
        "reviewed_at": row["reviewed_at"],
        "review_comment": row["review_comment"],
    }
