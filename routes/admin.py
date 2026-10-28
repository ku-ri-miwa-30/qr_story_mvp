"""
admin.py — 管理画面（HTML）

どのルートも同じ形をしています：
    1. フォームやURLから値を取り出す
    2. サービスの関数を呼ぶ（ここで権限・状態・入力のチェックが行われる）
    3. 成功したら flash ＋ redirect、エラー（AppError）なら flash して元の画面へ

POST の後に redirect する書き方を「PRGパターン（Post/Redirect/Get）」と呼びます。
ブラウザの更新ボタンで同じ登録が二重に送信されるのを防ぐ、定番の書き方です。
"""

import json
from datetime import datetime

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template,
                   request, send_file, url_for)

from errors import AppError, NotFoundError, ValidationError
from routes.auth import login_required
from routes.public import story_url
from services import (audit_service, auth_service, permissions, product_service,
                      qr_service, transfer_service, workflow_service)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
def require_login():
    """このブループリント（/admin/...）の全ページをログイン必須にする。"""
    return None


@bp.errorhandler(AppError)
def handle_app_error(error):
    """サービスで起きた業務エラーを、画面上のメッセージにして元のページへ戻す。"""
    if isinstance(error, NotFoundError):
        return render_template("404.html"), 404
    flash(error.message, "error")
    for d in error.details:
        flash(d["message"], "error")
    return redirect(request.referrer or url_for("admin.product_list"))


def _product_form():
    return {field: request.form.get(field, "") for field in
            ("name", "producer_name", "origin", "catchcopy", "story", "producer_message")}


# =====================================================================
# 商品
# =====================================================================
@bp.route("/")
def product_list():
    status = request.args.get("status") or None
    return render_template(
        "admin/product_list.html",
        products=product_service.list_products(status),
        current_status=status,
        counts=product_service.status_counts(),
    )


@bp.route("/products/new", methods=["GET", "POST"])
def product_new():
    if request.method == "POST":
        form = _product_form()
        try:
            image = product_service.save_image(request.files.get("image"),
                                               current_app.config["UPLOAD_FOLDER"])
            product_id = product_service.create_product(g.current_user, form, image)
        except ValidationError as e:
            # 入力エラーのときは、書いた内容を残したままフォームを再表示する
            return render_template("admin/product_form.html", product=form, is_new=True,
                                   errors=e.details or [{"message": e.message}])
        flash("下書きとして登録しました。記録を追加して、準備ができたら承認申請してください。", "success")
        return redirect(url_for("admin.product_detail", product_id=product_id))
    return render_template("admin/product_form.html", product={}, is_new=True, errors=[])


@bp.route("/products/<int:product_id>")
def product_detail(product_id):
    product = product_service.get_product(product_id)
    return render_template(
        "admin/product_detail.html",
        product=product,
        events=product_service.get_events(product_id),
        stats=product_service.scan_stats(product_id),
        editable=product["status"] in product_service.EDITABLE_STATUSES,
        actions=workflow_service.available_actions(g.current_user, product),
        history=workflow_service.history(product_id),
        logs=(audit_service.search(g.current_user, target_type="product", target_id=product_id, limit=20)
              if permissions.can(g.current_user, "audit.read") else []),
    )


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id):
    product = product_service.get_product(product_id)
    if request.method == "POST":
        form = _product_form()
        try:
            image = product_service.save_image(request.files.get("image"),
                                               current_app.config["UPLOAD_FOLDER"])
            if image:
                image_change = image
            elif request.form.get("remove_image"):
                image_change = None
            else:
                image_change = product_service.KEEP_IMAGE
            product_service.update_product(g.current_user, product_id, form,
                                           request.form.get("version"), image_change)
        except ValidationError as e:
            return render_template("admin/product_form.html",
                                   product={**dict(product), **form}, is_new=False,
                                   errors=e.details or [{"message": e.message}])
        flash("商品情報を保存しました。", "success")
        return redirect(url_for("admin.product_detail", product_id=product_id))

    if product["status"] not in product_service.EDITABLE_STATUSES:
        flash("この状態の商品は編集できません。", "error")
        return redirect(url_for("admin.product_detail", product_id=product_id))
    return render_template("admin/product_form.html", product=product, is_new=False, errors=[])


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
def product_delete(product_id):
    product_service.delete_product(g.current_user, product_id)
    flash("商品を削除しました（監査ログには削除した内容が残っています）。", "success")
    return redirect(url_for("admin.product_list"))


@bp.route("/products/<int:product_id>/events", methods=["POST"])
def event_add(product_id):
    product_service.add_event(g.current_user, product_id, request.form)
    flash("記録を追加しました。", "success")
    return redirect(url_for("admin.product_detail", product_id=product_id) + "#timeline")


@bp.route("/events/<int:event_id>/delete", methods=["POST"])
def event_delete(event_id):
    product_id = product_service.delete_event(g.current_user, event_id)
    flash("記録を削除しました。", "success")
    return redirect(url_for("admin.product_detail", product_id=product_id) + "#timeline")


# =====================================================================
# 申請・承認
# =====================================================================
@bp.route("/products/<int:product_id>/workflow/<action>", methods=["POST"])
def workflow(product_id, action):
    workflow_service.perform(g.current_user, product_id, action, request.form.get("comment", ""))
    label = workflow_service.TRANSITIONS[action]["label"]
    flash(f"「{label}」を行いました。", "success")
    return redirect(url_for("admin.product_detail", product_id=product_id))


@bp.route("/approvals")
def approvals():
    status = request.args.get("status", "pending") or None
    if status == "all":
        status = None
    return render_template("admin/approvals.html",
                           requests=workflow_service.list_requests(status),
                           current_status=status or "all")


# =====================================================================
# 監査ログ
# =====================================================================
@bp.route("/audit-logs")
def audit_logs():
    filters = {
        "action": request.args.get("action") or None,
        "actor": request.args.get("actor") or None,
    }
    rows = audit_service.search(g.current_user, limit=300, **filters)
    return render_template("admin/audit_logs.html", logs=rows, filters=filters)


# =====================================================================
# QR・ラベル
# =====================================================================
@bp.route("/products/<int:product_id>/qr.png")
def product_qr(product_id):
    product = product_service.get_product(product_id)
    return send_file(
        qr_service.make_png(story_url(product["slug"])),
        mimetype="image/png",
        as_attachment=request.args.get("download") == "1",
        download_name=f"story-qr-{product['slug']}.png",
    )


@bp.route("/products/<int:product_id>/label")
def product_label(product_id):
    product = product_service.get_product(product_id)
    count = request.args.get("count", default=12, type=int)
    count = max(1, min(count or 12, 60))
    return render_template("admin/label.html", product=product, count=count)


# =====================================================================
# データ連携（JSONファイルのエクスポート／インポート）
# =====================================================================
@bp.route("/transfer")
def transfer():
    return render_template("admin/transfer.html", result=None, errors=[])


@bp.route("/transfer/export")
def transfer_export():
    data = transfer_service.export_all(g.current_user)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"story-qr-products-{datetime.now():%Y%m%d-%H%M%S}.json"
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@bp.route("/transfer/import", methods=["POST"])
def transfer_import():
    file = request.files.get("file")
    try:
        if not file or not file.filename:
            raise ValidationError("JSONファイルを選んでください。")
        try:
            payload = json.load(file.stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValidationError(f"JSONとして読み込めませんでした（{e}）。")
        result = transfer_service.import_products(g.current_user, payload)
    except ValidationError as e:
        return render_template("admin/transfer.html", result=None,
                               summary=e.message, errors=e.details), 422
    flash(f"{result['created']} 件の商品を下書きとして取り込みました。", "success")
    return render_template("admin/transfer.html", result=result, errors=[])


# =====================================================================
# アカウント（APIトークン）
# =====================================================================
@bp.route("/account")
def account():
    return render_template("admin/account.html", new_token=None)


@bp.route("/account/token", methods=["POST"])
def issue_token():
    token = auth_service.issue_api_token(g.current_user)
    # トークンは1回だけ表示する（redirect すると消えてしまうので、そのまま表示）
    return render_template("admin/account.html", new_token=token)
