"""
public.py — ログイン不要のページ（お客さん向け物語ページ・画像・ヘルスチェック）
"""

from flask import (Blueprint, abort, current_app, g, redirect, render_template, request,
                   send_from_directory, url_for)

from services import product_service

bp = Blueprint("public", __name__)


def story_url(slug):
    """物語ページの完全なURL（QRコードの中身）。"""
    base = current_app.config["BASE_URL"]
    if base:
        return base + url_for("public.story", slug=slug)
    return url_for("public.story", slug=slug, _external=True)


def image_url(filename, external=False):
    if not filename:
        return None
    return url_for("public.uploaded_file", filename=filename, _external=external)


@bp.route("/")
def index():
    return redirect(url_for("admin.product_list"))


@bp.route("/healthz")
def healthz():
    """Render が「アプリが生きているか」を確認するためのURL。"""
    return {"status": "ok"}


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # send_from_directory は「../」などでフォルダの外を読まれないよう安全に処理してくれる
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=86400)


@bp.route("/p/<slug>")
def story(slug):
    """
    お客さんがQRを読み取って開くページ。
    公開中の商品だけ表示。ログイン中の人は、どの状態でもプレビューできる（回数には数えない）。
    """
    product = product_service.find_by_slug(slug)
    is_staff = g.current_user is not None
    if product is None or (product["status"] != "published" and not is_staff):
        abort(404)

    if not is_staff:
        product_service.record_scan(product, request.headers.get("User-Agent"))

    return render_template(
        "story.html",
        product=product,
        events=product_service.get_events(product["id"]),
        is_staff=is_staff,
    )
