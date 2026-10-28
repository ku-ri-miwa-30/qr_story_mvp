"""
seed.py — 動作確認用のサンプル商品を登録するスクリプト

使い方：
    python seed.py

サービス層の関数をそのまま使って登録するので、
「編集者が登録 → 申請 → 承認者が承認」という本物と同じ流れでデータが入り、監査ログも残ります。
※ 登場する農園・人物はすべて架空です。
"""

from app import create_app
from repositories import users as users_repo
from services import product_service, workflow_service

SAMPLES = [
    {
        "name": "深蒸し煎茶「朝霧」",
        "producer_name": "やまなみ茶園",
        "origin": "静岡県牧之原市",
        "catchcopy": "霧が晴れる前の一番茶だけを、手で摘みました。",
        "story": (
            "牧之原台地の茶畑は、春の朝になると川から上がる霧にすっぽり包まれます。\n"
            "この霧が強い日差しをやわらげ、渋みの少ない、まろやかな新芽を育ててくれます。\n\n"
            "「朝霧」は、その一番茶の中でも、霧が晴れる前に摘んだ芽だけを使っています。\n"
            "祖父の代から続く深蒸し製法で、色は濃く、味はやさしく仕上げました。"
        ),
        "producer_message": "お湯は少し冷まして、70℃くらいで淹れると甘みがよく出ます。",
        "events": [
            ("2026-02-10", "茶園の土づくり", "冬のあいだに有機肥料をすき込み、春に備えます。"),
            ("2026-04-28", "一番茶の手摘み", "霧の残る早朝5時から、一芯二葉だけを手で摘み取りました。"),
            ("2026-04-28", "蒸し・揉み・乾燥", "摘んだその日のうちに、自社工場で荒茶に仕上げます。"),
            ("2026-05-15", "仕上げ・袋詰め", "選別と火入れをして香りを整え、袋詰めしました。"),
        ],
    },
    {
        "name": "朝摘みいちご 紅ほっぺ",
        "producer_name": "ひだまりベリー農園",
        "origin": "静岡県掛川市",
        "catchcopy": "いちばん甘い、朝の5時に摘んでいます。",
        "story": (
            "いちごは夜のあいだに糖をため込み、朝にいちばん甘くなります。\n"
            "だから私たちは、毎朝まだ暗いうちからハウスに入ります。\n\n"
            "完熟まで待ってから摘むので、日持ちは長くありません。\n"
            "そのかわり、届いたその日に食べてほしい味をお約束します。"
        ),
        "producer_message": "冷蔵庫から出して10分ほど置くと、香りと甘みがぐっと立ちます。",
        "events": [
            ("2025-09-20", "苗の定植", "夏のあいだに育てた苗を、ハウスに植え付けました。"),
            ("2025-11-05", "ミツバチの導入", "ハウスにミツバチを放ち、花の受粉を手伝ってもらいます。"),
            ("2026-01-12", "朝摘み・出荷", "完熟した実だけを選んで摘み、その日のうちに出荷しました。"),
        ],
    },
]


def main():
    app = create_app()
    # Flask の外（スクリプト）から DB を使うときは app_context() の中で実行する
    with app.app_context():
        editor = users_repo.find_by_username("editor")
        approver = users_repo.find_by_username("approver")

        existing = {p["name"] for p in product_service.list_products()}
        for i, sample in enumerate(SAMPLES):
            if sample["name"] in existing:
                print(f"スキップ（登録済み）: {sample['name']}")
                continue

            data = {k: v for k, v in sample.items() if k != "events"}
            product_id = product_service.create_product(editor, data)
            for event_date, title, description in sample["events"]:
                product_service.add_event(editor, product_id, {
                    "event_date": event_date, "title": title, "description": description})

            # 1つ目は「公開中」、2つ目は「承認待ち」にして、承認画面を試せるようにする
            workflow_service.perform(editor, product_id, "submit", "確認をお願いします。")
            if i == 0:
                workflow_service.perform(approver, product_id, "approve", "内容OKです。公開します。")
            print(f"登録しました: {sample['name']}")

    print("完了！ python app.py で起動して http://127.0.0.1:5000 を開いてください。")


if __name__ == "__main__":
    main()
