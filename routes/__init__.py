"""
routes/ — ルート層（コントローラー）：「リクエストを受けてレスポンスを返す場所」

ルール：
    ・やることは「入力を受け取る → サービスを呼ぶ → 結果を画面/JSONにして返す」だけ
    ・業務ルールや SQL は書かない（services/ と repositories/ に任せる）

    auth.py   … ログイン・ログアウト、CSRF対策
    admin.py  … 管理画面（HTML）
    api.py    … REST API（JSON）  /api/v1/...
    public.py … お客さん向けの物語ページ

Java（Spring）の案件でいう Controller / RestController にあたる層です。
"""
