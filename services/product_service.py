"""
product_service.py — 商品と「できるまでの記録」に関する業務ルール

すべての「書き込み」関数は、
    ① 権限チェック（permissions.require）
    ② 存在・状態チェック（無ければ 404、編集できない状態なら 409）
    ③ 入力チェック（validate_*）
    ④ トランザクションの中で「DB更新 ＋ 監査ログ」
という同じ順番で書いています。この型を覚えると、申請・承認システムでも同じように書けます。
"""

import os
import secrets
from datetime import date, datetime, timedelta

from db import transaction
from errors import ConflictError, NotFoundError, ValidationError
from repositories import products as products_repo
from services import audit_service, permissions

# ---------------------------------------------------------------------
# 状態（ステータス）の定義
# ---------------------------------------------------------------------
STATUS_LABELS = {
    "draft": "下書き",
    "pending": "承認待ち",
    "published": "公開中",
    "rejected": "差し戻し",
}
# 編集してよい状態。承認待ち・公開中は、承認した内容から勝手に変わらないよう編集不可にする
EDITABLE_STATUSES = {"draft", "rejected"}

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# 入力項目ごとの最大文字数（DB設計書・API仕様にも同じ値を書く）
MAX_LENGTHS = {
    "name": 100,
    "producer_name": 100,
    "origin": 100,
    "catchcopy": 60,
    "story": 5000,
    "producer_message": 1000,
}
REQUIRED_FIELDS = {"name": "商品名", "producer_name": "生産者名"}


# =====================================================================
# 入力チェック（バリデーション）
# =====================================================================
def validate_product(raw):
    """
    画面のフォームでも API の JSON でも、同じ関数でチェックする。
    問題があれば ValidationError（details に項目ごとのエラー）を投げる。
    問題なければ「きれいにした値」の辞書を返す。
    """
    if not isinstance(raw, dict):
        raise ValidationError("商品データの形式が正しくありません。")

    cleaned, details = {}, []
    for field in products_repo.EDITABLE_COLUMNS:
        value = raw.get(field)
        if value is None:
            value = ""
        if not isinstance(value, str):
            details.append({"field": field, "message": "文字列で指定してください。"})
            continue
        value = value.strip()
        if field in REQUIRED_FIELDS and not value:
            details.append({"field": field, "message": f"{REQUIRED_FIELDS[field]}は必須です。"})
        if len(value) > MAX_LENGTHS[field]:
            details.append({"field": field, "message": f"{MAX_LENGTHS[field]}文字以内にしてください。"})
        cleaned[field] = value

    if details:
        raise ValidationError("入力内容に誤りがあります。", details)
    return cleaned


def validate_event(raw):
    if not isinstance(raw, dict):
        raise ValidationError("記録データの形式が正しくありません。")
    details = []
    event_date = str(raw.get("event_date") or "").strip()
    title = str(raw.get("title") or "").strip()
    description = str(raw.get("description") or "").strip()
    try:
        datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        details.append({"field": "event_date", "message": "日付は YYYY-MM-DD の形式で指定してください。"})
    if not title:
        details.append({"field": "title", "message": "出来事のタイトルは必須です。"})
    elif len(title) > 100:
        details.append({"field": "title", "message": "タイトルは100文字以内にしてください。"})
    if len(description) > 1000:
        details.append({"field": "description", "message": "説明は1000文字以内にしてください。"})
    if details:
        raise ValidationError("記録の入力内容に誤りがあります。", details)
    return {"event_date": event_date, "title": title, "description": description}


# =====================================================================
# 変換（DBの行 → API で返す辞書）
# =====================================================================
def to_dict(row, events=None):
    """
    DB の行をそのまま外に出さず、「外に見せる形」に詰め替える。
    （内部の列を勝手に公開しない／列名を変えても API の形は保てる）
    """
    data = {
        "id": row["id"],
        "slug": row["slug"],
        **{field: row[field] for field in products_repo.EDITABLE_COLUMNS},
        "image_filename": row["image_filename"],
        "status": row["status"],
        "status_label": STATUS_LABELS[row["status"]],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if "scan_count" in row.keys():
        data["scan_count"] = row["scan_count"]
    if events is not None:
        data["events"] = [event_to_dict(e) for e in events]
    return data


def event_to_dict(row):
    return {
        "id": row["id"],
        "event_date": row["event_date"],
        "title": row["title"],
        "description": row["description"],
    }


# =====================================================================
# 読み取り
# =====================================================================
def list_products(status=None):
    if status and status not in STATUS_LABELS:
        raise ValidationError(f"status は {', '.join(STATUS_LABELS)} のいずれかで指定してください。")
    return products_repo.find_all(status)


def get_product(product_id):
    product = products_repo.find_by_id(product_id)
    if product is None:
        raise NotFoundError("商品が見つかりません。")
    return product


def get_events(product_id):
    return products_repo.find_events(product_id)


def find_by_slug(slug):
    return products_repo.find_by_slug(slug)


def status_counts():
    return products_repo.count_by_status()


def _require_editable(product):
    if product["status"] not in EDITABLE_STATUSES:
        raise ConflictError(
            f"「{STATUS_LABELS[product['status']]}」の商品は編集できません。"
            "編集するには、申請を取り下げるか公開を停止して下書きに戻してください。"
        )


# =====================================================================
# 書き込み
# =====================================================================
def _new_slug():
    while True:
        slug = secrets.token_urlsafe(6)
        if not products_repo.slug_exists(slug):
            return slug


def create_product(user, raw, image_filename=None):
    """商品を「下書き」で登録し、新しい id を返す。"""
    permissions.require(user, "product.write")
    data = validate_product(raw)
    with transaction():
        product_id = products_repo.insert(data, _new_slug(), user["id"], image_filename)
        audit_service.record(user, "product.create", "product", product_id,
                             after={**data, "image_filename": image_filename})
    return product_id


# 「画像は変更しない」を表す目印（None は「画像を削除する」の意味で使うため、別の値を用意）
KEEP_IMAGE = object()


def update_product(user, product_id, raw, expected_version, image_filename=KEEP_IMAGE):
    permissions.require(user, "product.write")
    product = get_product(product_id)
    _require_editable(product)
    data = validate_product(raw)

    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        raise ValidationError("version（更新前のバージョン番号）を指定してください。",
                              [{"field": "version", "message": "整数で指定してください。"}])

    new_image = product["image_filename"] if image_filename is KEEP_IMAGE else image_filename

    with transaction():
        updated = products_repo.update(product_id, data, expected_version, new_image)
        if updated == 0:
            # 0行更新 = 画面を開いてから保存するまでの間に、誰かが先に更新した
            raise ConflictError(
                "ほかの人（または別のタブ）が先に更新しました。画面を開き直してから、もう一度編集してください。"
            )
        before = {k: product[k] for k in data} | {"image_filename": product["image_filename"]}
        after = data | {"image_filename": new_image}
        b, a = audit_service.diff(before, after)
        audit_service.record(user, "product.update", "product", product_id, before=b, after=a)


def delete_product(user, product_id):
    permissions.require(user, "product.delete")
    product = get_product(product_id)
    with transaction():
        products_repo.delete(product_id)
        # 削除した内容をまるごと before に残す（後から「何を消したか」を確認できる）
        audit_service.record(user, "product.delete", "product", product_id,
                             before=to_dict(product, get_events(product_id)))


def add_event(user, product_id, raw):
    permissions.require(user, "product.write")
    product = get_product(product_id)
    _require_editable(product)
    event = validate_event(raw)
    with transaction():
        event_id = products_repo.insert_event(product_id, event)
        products_repo.touch(product_id)
        audit_service.record(user, "timeline_event.create", "product", product_id,
                             after={"event_id": event_id, **event})
    return event_id


def delete_event(user, event_id):
    permissions.require(user, "product.write")
    event = products_repo.find_event(event_id)
    if event is None:
        raise NotFoundError("記録が見つかりません。")
    product = get_product(event["product_id"])
    _require_editable(product)
    with transaction():
        products_repo.delete_event(event_id)
        products_repo.touch(product["id"])
        audit_service.record(user, "timeline_event.delete", "product", product["id"],
                             before={"event_id": event_id, **event_to_dict(event)})
    return product["id"]


# =====================================================================
# 画像
# =====================================================================
def save_image(file_storage, upload_folder):
    """
    アップロードされた画像を保存してファイル名を返す。画像が無ければ None。
    ファイル名はランダムに付け直す（上書き防止・危険なファイル名対策）。
    """
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError("画像は png / jpg / jpeg / gif / webp のみアップロードできます。",
                              [{"field": "image", "message": "対応していない形式です。"}])
    os.makedirs(upload_folder, exist_ok=True)
    new_name = f"{secrets.token_hex(8)}.{ext}"
    file_storage.save(os.path.join(upload_folder, new_name))
    return new_name


# =====================================================================
# QRの読み取り記録
# =====================================================================
def record_scan(product, user_agent):
    with transaction():
        products_repo.insert_scan(product["id"], (user_agent or "")[:300])


def scan_stats(product_id, days=7):
    """合計と、直近 days 日の日別件数（0件の日も含める）を返す。"""
    counts = products_repo.daily_scans(product_id, days)
    today = date.today()
    daily = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        daily.append({"date": d.isoformat(), "label": d.strftime("%m/%d"),
                      "count": counts.get(d.isoformat(), 0)})
    return {"total": products_repo.count_scans(product_id), "daily": daily}
