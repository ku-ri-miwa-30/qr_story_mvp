"""
transfer_service.py — JSONファイルによる「システム間のデータ授受」

■ エクスポート（このシステム → 外部）
    全商品と年表を、決まった形式の JSON にして渡す。

■ インポート（外部 → このシステム）
    外部から受け取った JSON を取り込んで、商品を「下書き」として登録する。

    ファイル連携の実務で大事な3つのポイントを入れています：
    1. 形式チェック … 先頭の "format" と "format_version" で「想定したファイルか」を確認
    2. 全件チェック → 全件登録（オール・オア・ナッシング）
         1件でもエラーがあれば1件も登録しない。何行目の何がダメかを全部返す。
         「途中まで登録された」状態だと、どこから再送すればいいか分からなくなるため。
    3. 取り込んだデータは「下書き」にする … 外部データをそのまま公開せず、承認フローを通す

■ 2つの取り込みモード（ファイルの "mode"）
    "create"（省略時） … 新しい商品として登録。必ず「下書き」になり、公開URL（slug）も新しく作る
    "restore"          … バックアップからの復元（承認者のみ）。
                         公開URL（slug）と状態（公開中など）をファイルのまま戻す。
                         Render の無料プランは再起動でDBが消えるので、
                         「エクスポートで保存 → 消えたら restore で戻す」ために使う。
                         slug を引き継ぐので、印刷済みのQRコードもそのまま使える。

■ ファイルの形（format_version 1）
    {
      "format": "story-qr-products",
      "format_version": 1,
      "exported_at": "2026-10-28T12:00:00",
      "products": [
        {
          "slug": "k3Jx9aQw",          ← restore のときだけ使う（公開URL /p/k3Jx9aQw）
          "status": "published",       ← restore のときだけ使う
          "name": "深蒸し煎茶「朝霧」",
          "producer_name": "やまなみ茶園",
          "origin": "静岡県牧之原市",
          "catchcopy": "...", "story": "...", "producer_message": "...",
          "events": [
            {"event_date": "2026-04-28", "title": "一番茶の手摘み", "description": "..."}
          ]
        }
      ]
    }
"""

from datetime import datetime

from db import transaction
from errors import ValidationError
from repositories import products as products_repo
from services import audit_service, permissions, product_service

FORMAT_NAME = "story-qr-products"
FORMAT_VERSION = 1
MAX_IMPORT_PRODUCTS = 500  # 一度に取り込める上限（巨大ファイルでサーバーが固まるのを防ぐ）


def export_all(user):
    """全商品を JSON にできる辞書で返す（画像ファイル本体は含めない）。"""
    products = []
    for row in products_repo.find_all():
        item = {"slug": row["slug"], "status": row["status"]}
        item.update({field: row[field] for field in products_repo.EDITABLE_COLUMNS})
        item["events"] = [
            {"event_date": e["event_date"], "title": e["title"], "description": e["description"]}
            for e in products_repo.find_events(row["id"])
        ]
        products.append(item)

    with transaction():
        audit_service.record(user, "transfer.export", after={"product_count": len(products)})

    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "products": products,
    }


# restore のとき、どの状態で戻すか。承認待ちは申請データが無いので下書きに戻す
RESTORE_STATUS = {"draft": "draft", "pending": "draft", "published": "published", "rejected": "rejected"}


def import_products(user, payload):
    """
    戻り値：{"created": 件数, "product_ids": [...], "mode": "create" | "restore"}
    エラーがあれば ValidationError（details に「products[2].name」のような場所つきで全件）
    """
    permissions.require(user, "transfer.import")

    # ---- 1. ファイル全体の形式チェック ----
    if not isinstance(payload, dict):
        raise ValidationError("JSONの一番外側はオブジェクト（{ }）にしてください。")
    if payload.get("format") != FORMAT_NAME:
        raise ValidationError(f'"format" が "{FORMAT_NAME}" ではありません。取り込めるファイルか確認してください。')
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValidationError(f'"format_version" は {FORMAT_VERSION} のみ対応しています。')
    items = payload.get("products")
    if not isinstance(items, list) or not items:
        raise ValidationError('"products" に1件以上の商品を配列で入れてください。')
    if len(items) > MAX_IMPORT_PRODUCTS:
        raise ValidationError(f"一度に取り込めるのは {MAX_IMPORT_PRODUCTS} 件までです。")
    mode = payload.get("mode") or "create"
    if mode not in ("create", "restore"):
        raise ValidationError('"mode" は "create" か "restore" を指定してください。')
    if mode == "restore":
        # 承認を通さずに「公開中」に戻せてしまうので、承認者だけに許可する
        permissions.require(user, "transfer.restore")

    # ---- 2. 全件チェック（ここでは1件も登録しない）----
    details, validated = [], []
    seen_slugs = set()
    for i, item in enumerate(items):
        data = None
        try:
            data = product_service.validate_product(item)
        except ValidationError as e:
            details += [{"field": f"products[{i}].{d['field']}", "message": d["message"]}
                        for d in (e.details or [{"field": "", "message": e.message}])]
            if not isinstance(item, dict):
                continue
        # 商品にエラーがあっても記録のチェックは続ける（エラーを一度に全部返すため）
        events = item.get("events") or []
        if not isinstance(events, list):
            details.append({"field": f"products[{i}].events", "message": "配列で指定してください。"})
            continue
        cleaned_events = []
        for j, ev in enumerate(events):
            try:
                cleaned_events.append(product_service.validate_event(ev))
            except ValidationError as e:
                details += [{"field": f"products[{i}].events[{j}].{d['field']}", "message": d["message"]}
                            for d in (e.details or [{"field": "", "message": e.message}])]
        slug, status = None, "draft"
        if mode == "restore":
            slug = item.get("slug")
            status = RESTORE_STATUS.get(item.get("status"))
            if not isinstance(slug, str) or not product_service.SLUG_PATTERN.match(slug):
                details.append({"field": f"products[{i}].slug",
                                "message": "英数字・-・_ の4〜32文字で指定してください。"})
            elif slug in seen_slugs or products_repo.slug_exists(slug):
                details.append({"field": f"products[{i}].slug",
                                "message": f"公開URL「{slug}」は既に使われています（重複して取り込めません）。"})
            else:
                seen_slugs.add(slug)
            if status is None:
                details.append({"field": f"products[{i}].status",
                                "message": "draft / pending / published / rejected のいずれかで指定してください。"})
        validated.append((data, cleaned_events, slug, status))

    if details:
        raise ValidationError(f"{len(details)} 件のエラーがあるため、1件も取り込んでいません。", details)

    # ---- 3. 全件登録（1つのトランザクション）----
    product_ids = []
    with transaction():
        for data, events, slug, status in validated:
            product_id = product_service.create_product(user, data, slug=slug)
            for ev in events:
                products_repo.insert_event(product_id, ev)
            if status != "draft":
                products_repo.update_status(product_id, status)
            product_ids.append(product_id)
        audit_service.record(user, f"transfer.{'restore' if mode == 'restore' else 'import'}",
                             after={"created": len(product_ids), "product_ids": product_ids})

    return {"created": len(product_ids), "product_ids": product_ids, "mode": mode}
