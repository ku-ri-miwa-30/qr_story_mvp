"""
api.py — REST API（JSON）  ベースURL: /api/v1

■ REST の基本ルール（このAPIで守っていること）
    ・URL は「もの（リソース）」を名詞の複数形で表す   /products, /products/3/events
    ・操作は HTTP メソッドで表す
        GET 取得 / POST 作成 / PATCH 一部更新 / DELETE 削除
    ・結果は HTTP ステータスコードで表す
        200 OK / 201 作成した / 204 成功・返す中身なし
        401 認証が必要 / 403 権限なし / 404 無い / 409 状態が合わない / 422 入力エラー
    ・エラーの JSON の形を全APIでそろえる
        {"error": {"code": "VALIDATION_ERROR", "message": "...", "details": [...]}}
    ・URL に /v1 を付けておく（将来、互換性のない変更をするときに /v2 を作れる）

■ 「承認する」のような、作成・更新・削除に当てはまらない操作
    POST /products/3/approve のように「動詞のサブリソース」にするのがよくある設計。

■ 認証
    ヘッダーに  Authorization: Bearer <APIトークン>  を付ける。
    トークンは管理画面の「アカウント」で発行する。

実際に叩いてみるには、VS Code の拡張機能「REST Client」で api.http を開いてください。
"""

import json
from datetime import datetime

from flask import Blueprint, Response, g, jsonify, request, url_for

from errors import AppError, UnauthorizedError, ValidationError
from routes.public import image_url, story_url
from services import (audit_service, auth_service, product_service, transfer_service,
                      workflow_service)

bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------
# 共通処理
# ---------------------------------------------------------------------
@bp.before_request
def authenticate():
    """全APIの前に、APIトークンで「誰か」を確認する。"""
    g.via = "api"
    # 画面のログイン（Cookie）は API では使わない。トークンだけで判断する
    g.current_user = None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise UnauthorizedError("Authorization ヘッダーに「Bearer <APIトークン>」を指定してください。")
    user = auth_service.user_from_token(header.removeprefix("Bearer ").strip())
    if user is None:
        raise UnauthorizedError("APIトークンが正しくないか、再発行により無効になっています。")
    g.current_user = user


@bp.errorhandler(AppError)
def handle_app_error(error):
    """業務エラーを、決まった形の JSON ＋ ステータスコードにして返す。"""
    return jsonify(error.to_dict()), error.status_code


def json_body():
    """リクエストの JSON ボディを辞書で取り出す。JSON でなければ 422。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("リクエストボディは JSON オブジェクトで送ってください"
                              "（ヘッダー Content-Type: application/json が必要です）。")
    return data


def product_json(product_id):
    """商品1件を、URL類も足した API の形にする。"""
    product = product_service.get_product(product_id)
    data = product_service.to_dict(product, product_service.get_events(product_id))
    data["story_url"] = story_url(product["slug"])
    data["image_url"] = image_url(product["image_filename"], external=True)
    return data


# ---------------------------------------------------------------------
# 自分の情報
# ---------------------------------------------------------------------
@bp.get("/me")
def me():
    u = g.current_user
    return jsonify({"id": u["id"], "username": u["username"],
                    "display_name": u["display_name"], "role": u["role"]})


# ---------------------------------------------------------------------
# 商品
# ---------------------------------------------------------------------
@bp.get("/products")
def list_products():
    rows = product_service.list_products(request.args.get("status") or None)
    return jsonify({"items": [product_service.to_dict(r) for r in rows], "count": len(rows)})


@bp.post("/products")
def create_product():
    product_id = product_service.create_product(g.current_user, json_body())
    # 201 Created のときは、作ったもののURLを Location ヘッダーで返すのが REST の作法
    return (jsonify(product_json(product_id)), 201,
            {"Location": url_for("api.get_product", product_id=product_id)})


@bp.get("/products/<int:product_id>")
def get_product(product_id):
    return jsonify(product_json(product_id))


@bp.patch("/products/<int:product_id>")
def update_product(product_id):
    """
    一部更新。送られてきた項目だけを変える（送られなかった項目は今の値のまま）。
    楽観的ロックのため "version"（GET で取った値）が必須。ずれていたら 409。
    """
    body = json_body()
    current = product_service.get_product(product_id)
    merged = {field: body.get(field, current[field]) for field in
              ("name", "producer_name", "origin", "catchcopy", "story", "producer_message")}
    product_service.update_product(g.current_user, product_id, merged, body.get("version"))
    return jsonify(product_json(product_id))


@bp.delete("/products/<int:product_id>")
def delete_product(product_id):
    product_service.delete_product(g.current_user, product_id)
    return "", 204


# ---------------------------------------------------------------------
# できるまでの記録（商品のサブリソース）
# ---------------------------------------------------------------------
@bp.get("/products/<int:product_id>/events")
def list_events(product_id):
    product_service.get_product(product_id)  # 商品が無ければ 404
    events = product_service.get_events(product_id)
    return jsonify({"items": [product_service.event_to_dict(e) for e in events]})


@bp.post("/products/<int:product_id>/events")
def add_event(product_id):
    event_id = product_service.add_event(g.current_user, product_id, json_body())
    return jsonify({"id": event_id, "product_id": product_id}), 201


@bp.delete("/events/<int:event_id>")
def delete_event(event_id):
    product_service.delete_event(g.current_user, event_id)
    return "", 204


# ---------------------------------------------------------------------
# 申請・承認フロー
#   POST /products/3/submit    {"comment": "確認お願いします"}
#   POST /products/3/approve   {"comment": "OKです"}
#   POST /products/3/reject    {"comment": "産地を正確に書いてください"}  ← comment 必須
#   POST /products/3/withdraw
#   POST /products/3/unpublish
# ---------------------------------------------------------------------
@bp.post("/products/<int:product_id>/<action>")
def workflow(product_id, action):
    if action not in workflow_service.TRANSITIONS:
        raise ValidationError(f"不明な操作です: {action}")
    body = request.get_json(silent=True) or {}
    comment = body.get("comment", "") if isinstance(body, dict) else ""
    workflow_service.perform(g.current_user, product_id, action, comment)
    return jsonify(product_json(product_id))


@bp.get("/approval-requests")
def list_approval_requests():
    rows = workflow_service.list_requests(request.args.get("status") or None)
    return jsonify({"items": [workflow_service.request_to_dict(r) for r in rows]})


# ---------------------------------------------------------------------
# 監査ログ（承認者のみ）
# ---------------------------------------------------------------------
@bp.get("/audit-logs")
def list_audit_logs():
    limit = min(request.args.get("limit", default=100, type=int) or 100, 500)
    rows = audit_service.search(
        g.current_user,
        action=request.args.get("action") or None,
        target_type=request.args.get("target_type") or None,
        target_id=request.args.get("target_id", type=int),
        limit=limit,
    )
    return jsonify({"items": [audit_service.to_dict(r) for r in rows]})


# ---------------------------------------------------------------------
# ファイル連携
#   GET  /export … JSONファイルとしてダウンロード
#   POST /import … JSON ボディ、または multipart/form-data の file で送る
# ---------------------------------------------------------------------
@bp.get("/export")
def export_products():
    data = transfer_service.export_all(g.current_user)
    filename = f"story-qr-products-{datetime.now():%Y%m%d-%H%M%S}.json"
    return Response(json.dumps(data, ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@bp.post("/import")
def import_products():
    if "file" in request.files:
        try:
            payload = json.load(request.files["file"].stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValidationError(f"JSONとして読み込めませんでした（{e}）。")
    else:
        payload = json_body()
    result = transfer_service.import_products(g.current_user, payload)
    return jsonify(result), 201
