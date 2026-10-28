"""
tests/test_app.py — 自動テスト

実行方法（どちらでもOK）：
    python -m pytest                 # pytest を入れている場合（requirements-dev.txt）
    python -m unittest -v            # Python 標準だけで実行

テストは「仕様をコードで書いたもの」です。
特に、承認フローや監査ログのような「ルールが大事な機能」は、テストで守っておくと安心して直せます。
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        # テストごとに空のDBを一時フォルダに作る（本物のデータを汚さない）
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({
            "DATABASE": os.path.join(self.tmp, "test.db"),
            "UPLOAD_FOLDER": os.path.join(self.tmp, "uploads"),
            "TESTING": True,
        })
        self.client = self.app.test_client()
        self.editor_token = self._issue_token("editor")
        self.approver_token = self._issue_token("approver")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _issue_token(self, username):
        from repositories import users as users_repo
        from services import auth_service
        with self.app.app_context():
            return auth_service.issue_api_token(users_repo.find_by_username(username))

    def api(self, method, url, token, body=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.open(f"/api/v1{url}", method=method, json=body, headers=headers)

    def create_product(self, **overrides):
        body = {"name": "テスト茶", "producer_name": "テスト茶園", "story": "物語です。", **overrides}
        res = self.api("POST", "/products", self.editor_token, body)
        self.assertEqual(res.status_code, 201, res.get_json())
        return res.get_json()


class ApiBasicsTest(BaseTestCase):
    def test_トークンがないと401(self):
        res = self.api("GET", "/products", None)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"]["code"], "UNAUTHORIZED")

    def test_商品を作ると下書きになりLocationが返る(self):
        res = self.api("POST", "/products", self.editor_token,
                       {"name": "いちご", "producer_name": "ひだまり農園"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["status"], "draft")
        self.assertIn("/api/v1/products/", res.headers["Location"])

    def test_必須項目がないと422で項目ごとのエラー(self):
        res = self.api("POST", "/products", self.editor_token, {"name": ""})
        self.assertEqual(res.status_code, 422)
        fields = {d["field"] for d in res.get_json()["error"]["details"]}
        self.assertEqual(fields, {"name", "producer_name"})

    def test_存在しない商品は404のJSON(self):
        res = self.api("GET", "/products/9999", self.editor_token)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()["error"]["code"], "NOT_FOUND")

    def test_楽観的ロック_古いversionで更新すると409(self):
        p = self.create_product()
        ok = self.api("PATCH", f"/products/{p['id']}", self.editor_token,
                      {"name": "新しい名前", "version": p["version"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["version"], p["version"] + 1)
        stale = self.api("PATCH", f"/products/{p['id']}", self.editor_token,
                         {"name": "古い画面から保存", "version": p["version"]})
        self.assertEqual(stale.status_code, 409)

    def test_編集者は削除できない_承認者はできる(self):
        p = self.create_product()
        self.assertEqual(self.api("DELETE", f"/products/{p['id']}", self.editor_token).status_code, 403)
        self.assertEqual(self.api("DELETE", f"/products/{p['id']}", self.approver_token).status_code, 204)


class WorkflowTest(BaseTestCase):
    def test_申請から承認で公開され物語ページが見られる(self):
        p = self.create_product()
        # 公開前はお客さんには404
        self.assertEqual(self.client.get(f"/p/{p['slug']}").status_code, 404)

        res = self.api("POST", f"/products/{p['id']}/submit", self.editor_token, {"comment": "お願いします"})
        self.assertEqual(res.get_json()["status"], "pending")
        res = self.api("POST", f"/products/{p['id']}/approve", self.approver_token, {"comment": "OK"})
        self.assertEqual(res.get_json()["status"], "published")

        self.assertEqual(self.client.get(f"/p/{p['slug']}").status_code, 200)

    def test_承認待ちの商品は編集できない(self):
        p = self.create_product()
        self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        res = self.api("PATCH", f"/products/{p['id']}", self.editor_token,
                       {"name": "こっそり変更", "version": p["version"] + 1})
        self.assertEqual(res.status_code, 409)

    def test_編集者は承認できない(self):
        p = self.create_product()
        self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        res = self.api("POST", f"/products/{p['id']}/approve", self.editor_token)
        self.assertEqual(res.status_code, 403)

    def test_職務分掌_自分の申請は自分で承認できない(self):
        p = self.api("POST", "/products", self.approver_token,
                     {"name": "自作", "producer_name": "承認者", "story": "本文"}).get_json()
        self.api("POST", f"/products/{p['id']}/submit", self.approver_token)
        res = self.api("POST", f"/products/{p['id']}/approve", self.approver_token)
        self.assertEqual(res.status_code, 403)
        self.assertIn("職務分掌", res.get_json()["error"]["message"])

    def test_差し戻しには理由が必須_差し戻し後は再申請できる(self):
        p = self.create_product()
        self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        self.assertEqual(self.api("POST", f"/products/{p['id']}/reject", self.approver_token).status_code, 422)
        res = self.api("POST", f"/products/{p['id']}/reject", self.approver_token, {"comment": "産地を詳しく"})
        self.assertEqual(res.get_json()["status"], "rejected")
        res = self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        self.assertEqual(res.get_json()["status"], "pending")

    def test_物語が空だと申請できない(self):
        p = self.create_product(story="")
        res = self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        self.assertEqual(res.status_code, 422)

    def test_状態遷移表にない操作は409(self):
        p = self.create_product()
        res = self.api("POST", f"/products/{p['id']}/approve", self.approver_token)  # 下書きを承認
        self.assertEqual(res.status_code, 409)


class AuditTest(BaseTestCase):
    def test_操作が監査ログに残る(self):
        p = self.create_product()
        self.api("PATCH", f"/products/{p['id']}", self.editor_token,
                 {"origin": "静岡県", "version": p["version"]})
        self.api("POST", f"/products/{p['id']}/submit", self.editor_token)
        self.api("POST", f"/products/{p['id']}/approve", self.approver_token)

        res = self.api("GET", f"/audit-logs?target_type=product&target_id={p['id']}", self.approver_token)
        logs = res.get_json()["items"]
        actions = [log["action"] for log in logs]
        self.assertEqual(actions, ["product.approve", "product.submit", "product.update", "product.create"])

        update = logs[2]
        # 変わった項目だけが before / after に入っている
        self.assertEqual(update["before"], {"origin": ""})
        self.assertEqual(update["after"], {"origin": "静岡県"})
        self.assertEqual(update["via"], "api")
        self.assertEqual(logs[0]["actor"]["name"], "承認者（運営）")

    def test_編集者は監査ログを見られない(self):
        self.assertEqual(self.api("GET", "/audit-logs", self.editor_token).status_code, 403)

    def test_ログイン失敗も記録される(self):
        page = self.client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.client.post("/login", data={"username": "editor", "password": "wrong", "csrf_token": token})
        logs = self.api("GET", "/audit-logs?action=auth.login_failed", self.approver_token).get_json()["items"]
        self.assertEqual(len(logs), 1)


class TransferTest(BaseTestCase):
    def payload(self, products):
        return {"format": "story-qr-products", "format_version": 1, "products": products}

    def test_インポートした商品は下書きになる(self):
        res = self.api("POST", "/import", self.editor_token, self.payload([
            {"name": "A", "producer_name": "X",
             "events": [{"event_date": "2026-04-01", "title": "収穫"}]},
            {"name": "B", "producer_name": "Y"},
        ]))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["created"], 2)
        items = self.api("GET", "/products?status=draft", self.editor_token).get_json()["items"]
        self.assertEqual(len(items), 2)

    def test_1件でもエラーなら1件も登録しない(self):
        res = self.api("POST", "/import", self.editor_token, self.payload([
            {"name": "OK", "producer_name": "X"},
            {"name": "", "producer_name": "Y", "events": [{"event_date": "4月1日", "title": "収穫"}]},
        ]))
        self.assertEqual(res.status_code, 422)
        fields = {d["field"] for d in res.get_json()["error"]["details"]}
        self.assertIn("products[1].name", fields)
        self.assertIn("products[1].events[0].event_date", fields)
        self.assertEqual(self.api("GET", "/products", self.editor_token).get_json()["count"], 0)

    def test_ファイルでも取り込める_エクスポートと往復できる(self):
        self.create_product(name="往復テスト")
        exported = self.client.get("/api/v1/export",
                                   headers={"Authorization": f"Bearer {self.editor_token}"})
        self.assertIn("attachment", exported.headers["Content-Disposition"])
        res = self.client.post(
            "/api/v1/import",
            headers={"Authorization": f"Bearer {self.editor_token}"},
            data={"file": (io.BytesIO(exported.data), "export.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 201)
        names = [p["name"] for p in self.api("GET", "/products", self.editor_token).get_json()["items"]]
        self.assertEqual(names.count("往復テスト"), 2)

    def test_形式が違うファイルは拒否(self):
        res = self.api("POST", "/import", self.editor_token, {"products": []})
        self.assertEqual(res.status_code, 422)


class WebTest(BaseTestCase):
    def test_CSRFトークンなしのPOSTは受け付けない(self):
        res = self.client.post("/login", data={"username": "editor", "password": "editor"})
        self.assertEqual(res.status_code, 302)
        with self.client.session_transaction() as s:
            self.assertNotIn("user_id", s)

    def test_未ログインで管理画面はログインへ(self):
        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.location)


if __name__ == "__main__":
    unittest.main()
