# この商品の物語 — QRサービス MVP

商品ごとにQRコードを発行し、スマホで読み取ると **生産者の物語** と **商品ができるまでの記録（トレーサビリティ）** が表示されるWebサービスです。

商品は **下書き → 承認申請 → 承認** の流れで公開され、すべての操作が **監査ログ** に残ります。
画面のほかに **REST API（JSON）** と **JSONファイルでのデータ連携** も用意しています。

- **言語・技術**：Python 3.12 / Flask / SQLite / Jinja2
- **外部ライブラリ**：Flask・qrcode・gunicorn（本番サーバー）の3つだけ
- **設計書**：[docs/design.md](docs/design.md)（ER図・状態遷移図・API仕様・監査ログ設計）

---

## 1. VS Code で動かす

### 準備（最初の1回だけ）

1. このリポジトリを clone して VS Code で開く
   ```bash
   git clone https://github.com/ku-ri-miwa-30/qr_story_mvp.git
   code qr_story_mvp
   ```
2. 右下に「おすすめの拡張機能をインストールしますか？」と出たら **インストール**（Python・REST Client など）
3. VS Code のターミナル（`` Ctrl+` ``）で仮想環境を作ってライブラリを入れる

   ```bash
   # Mac / Linux
   python3 -m venv .venv
   source .venv/bin/activate

   # Windows（PowerShell）
   python -m venv .venv
   .venv\Scripts\Activate.ps1

   # 共通
   pip install -r requirements-dev.txt
   ```
4. VS Code 右下の Python のバージョン表示をクリックし、`.venv` のものを選ぶ

### 起動

```bash
python seed.py   # （任意）サンプル商品を2件入れる（1件は公開中、1件は承認待ち）
python app.py    # http://127.0.0.1:5000 を開く
```

または VS Code で **F5**（「Flask: アプリを起動」）。行番号の左をクリックしてブレークポイントを置けば、処理を止めて変数の中身を見られます。

### 初期アカウント（開発用）

| ユーザー名 | パスワード | 役割 | できること |
|---|---|---|---|
| `editor` | `editor` | 編集者（生産者） | 登録・編集・承認申請 |
| `approver` | `approver` | 承認者（運営） | 上記＋承認・差し戻し・削除・監査ログ |

**承認フローを試す**：`editor` でログイン → 商品を登録して「承認申請」→ ログアウト → `approver` でログイン → 「承認待ち」から承認。
自分が出した申請は自分では承認できません（職務分掌）。

### テスト

```bash
python -m pytest -v
```

VS Code 左の「テスト」（フラスコのアイコン）からも実行できます。テスト名は日本語で「仕様」をそのまま書いています。

### API を試す

1. 管理画面右上の「アカウント」→「トークンを発行する」
2. `api.http` を開いて、先頭の `@token =` にトークンを貼る
3. 各リクエストの上の **Send Request** をクリック

### スマホで試す

PCとスマホを同じWi-Fiにつなぎ、PCのIPアドレスを `BASE_URL` に設定して起動します。

```bash
# Mac: ipconfig getifaddr en0 / Windows: ipconfig で確認（例: 192.168.1.23）
BASE_URL=http://192.168.1.23:5000 python app.py            # Mac / Linux
$env:BASE_URL="http://192.168.1.23:5000"; python app.py    # Windows（PowerShell）
```

---

## 2. Render に公開する

### 料金について（大事）

| プラン | 月額 | データ（DB・写真） |
|---|---|---|
| Free | 0円 | **15分アクセスが無いだけで消える**（ディスクが一時的なため）。お試し専用 |
| Starter ＋ ディスク1GB | 約7ドル＋0.25ドル | 再起動・再デプロイしても残る ← `render.yaml` はこちら |

実際の商品にQRを貼るなら Starter にしてください（QRを読み取ったら「見つかりません」は致命的なので）。

### 手順

1. [Render](https://render.com) に GitHub アカウントでサインアップ
2. ダッシュボードで **New → Blueprint** → このリポジトリ（`qr_story_mvp`）を選ぶ
3. `render.yaml` が読み込まれ、入力欄が出るので設定する
   - `EDITOR_PASSWORD`：編集者のパスワード（推測されにくいもの）
   - `APPROVER_PASSWORD`：承認者のパスワード
   - `SECRET_KEY` は自動で作られます
4. **Apply** → 数分でデプロイ完了。`https://qr-story-mvp-xxxx.onrender.com` のようなURLが発行されます
5. 以後は GitHub の `main` に push するたびに自動で再デプロイされます

> 独自ドメイン（例：`story.example.jp`）を使うなら、Render の Settings → Custom Domains で設定し、環境変数 `BASE_URL` にそのURLを入れてください。**QRに埋め込まれるURLが変わる** ので、ラベル印刷はドメインを決めてからがおすすめです。

### 設定できる環境変数

| 名前 | 意味 | 初期値 |
|---|---|---|
| `APP_ENV` | `production` にすると本番の安全設定が有効 | `development` |
| `SECRET_KEY` | ログイン状態（session）の暗号化キー | 開発用の固定値（本番では起動しない） |
| `DATA_DIR` | DB と写真の保存先フォルダ | `./data` |
| `BASE_URL` | QRに埋め込むURLの先頭 | 空（アクセス中のURL） |
| `EDITOR_PASSWORD` / `APPROVER_PASSWORD` | 初回起動時に作るユーザーのパスワード | `editor` / `approver`（本番では起動しない） |

---

## 3. フォルダ構成（3層構造）

```
qr_story_mvp/
├── app.py                 ← 入口。create_app() でアプリを組み立てる
├── config.py              ← 設定（環境変数を読む）
├── db.py                  ← DB接続とトランザクション
├── errors.py              ← 業務エラー（422 / 403 / 404 / 409 …）
├── schema.sql             ← テーブル設計
├── seed.py                ← サンプルデータ投入
│
├── routes/                ← ① ルート層：リクエストを受けて返す
│   ├── admin.py               管理画面（HTML）
│   ├── api.py                 REST API（JSON）/api/v1
│   ├── auth.py                ログイン・CSRF対策
│   └── public.py              お客さん向け物語ページ
├── services/              ← ② サービス層：業務ルール
│   ├── product_service.py     商品・記録（入力チェック、編集可否）
│   ├── workflow_service.py    申請・承認（状態遷移表、職務分掌）
│   ├── audit_service.py       監査ログ
│   ├── transfer_service.py    JSONファイル連携
│   ├── auth_service.py        ログイン・APIトークン
│   ├── permissions.py         ロールと権限の表
│   └── qr_service.py          QRコード画像
├── repositories/          ← ③ リポジトリ層：SQL
│   ├── products.py / approvals.py / audit_logs.py / users.py
│
├── templates/             ← HTML（Jinja2）
├── static/style.css
├── tests/test_app.py      ← 自動テスト（22件）
├── docs/
│   ├── design.md              設計書
│   └── sample-import.json     インポート用サンプル
├── api.http               ← VS Code の REST Client で API を叩く
├── render.yaml            ← Render の設定
└── .vscode/               ← F5 実行・おすすめ拡張機能
```

**読む順番のおすすめ**：
`docs/design.md` → `schema.sql` → `services/workflow_service.py`（状態遷移表）→ `routes/api.py` → `services/product_service.py` → `repositories/products.py`

---

## 4. よくあるつまずき

| 症状 | 原因と対処 |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | 仮想環境が有効になっていない → `source .venv/bin/activate`（Windows は `.venv\Scripts\Activate.ps1`） |
| PowerShell で「スクリプトの実行が無効」 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` を1回実行 |
| 表の構成を変えたのに反映されない | `CREATE TABLE IF NOT EXISTS` は既存の表を変えない → 開発中なら `data/` フォルダを削除して作り直す |
| 「画面の有効期限が切れました」 | CSRF トークンが古い（ログアウトした別タブなど）→ ページを開き直す |
| 承認ボタンが出ない | 自分が出した申請は承認できない → もう一方のアカウントでログイン |
| 編集ボタンが出ない | 承認待ち・公開中は編集できない → 取り下げ／公開停止で下書きに戻す |
| API が 401 | `Authorization: Bearer <トークン>` が無い、またはトークンを再発行して古いものを使っている |
| Render で起動しない | ログに「EDITOR_PASSWORD…」→ 環境変数を設定する |

---

## 5. 次のステップ

- [ ] 生産者ごとのアカウント（自分の商品だけ見える）と招待機能
- [ ] 料金プラン（例：商品3つまで無料）→ Stripe 連携
- [ ] 物語ページの多言語化（訪日客・海外販売向け）
- [ ] ロット（収穫日・製造日）ごとのQR
- [ ] 物語ページに「購入はこちら」ボタン、お客さんの感想投稿
- [ ] 公開中のまま出せる「変更申請」、承認の多段化
- [ ] OpenAPI（Swagger）で API 仕様書を書く
- [ ] DB を PostgreSQL へ（利用者が増えたら）
