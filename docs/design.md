# 設計書 — この商品の物語 QR サービス

このドキュメントは「コードを読む前に全体像をつかむ」ためのものです。
実務の **申請・承認システム / REST API / ファイル連携** でもそのまま使える考え方で書いています。

> VS Code で図を表示するには、拡張機能「Markdown Preview Mermaid Support」を入れて、このファイルを開いた状態で `Ctrl+Shift+V`（Mac は `⌘+Shift+V`）を押してください。

---

## 目次

1. [全体像（システム構成）](#1-全体像システム構成)
2. [アーキテクチャ（3層構造）](#2-アーキテクチャ3層構造)
3. [DB設計](#3-db設計)
4. [申請・承認フロー（状態遷移）](#4-申請承認フロー状態遷移)
5. [権限設計（ロール）](#5-権限設計ロール)
6. [監査ログ設計](#6-監査ログ設計)
7. [REST API 設計](#7-rest-api-設計)
8. [ファイル連携（JSON）設計](#8-ファイル連携json設計)
9. [排他制御（楽観的ロック）](#9-排他制御楽観的ロック)
10. [セキュリティ対策](#10-セキュリティ対策)
11. [次の案件との対応表（Python）](#11-次の案件との対応表python)

---

## 1. 全体像（システム構成）

```mermaid
flowchart LR
    customer["お客さん（スマホ）"] -- "QRを読み取る" --> story["物語ページ<br/>/p/slug"]
    editor["編集者（生産者）"] -- "ブラウザ" --> admin["管理画面<br/>/admin"]
    approver["承認者（運営）"] -- "ブラウザ" --> admin
    other["他システム・バッチ"] -- "JSON / APIトークン" --> api["REST API<br/>/api/v1"]

    subgraph app["Flask アプリ（Render 無料プラン上で動く）"]
        story
        admin
        api
    end
    app --> db[("SQLite<br/>data/story_qr.db<br/>※無料プランでは一時的")]
    app --> files[["アップロード写真<br/>data/uploads<br/>※無料プランでは一時的"]]
    db -. "エクスポート（バックアップ）" .-> backup[["JSONファイル<br/>手元に保管"]]
    backup -. "復元（restore）" .-> db
```

### 公開環境の方針（Render 無料プラン）

| 項目 | 方針 |
|---|---|
| 位置づけ | **デモ・動作確認用**。お金をかけずに「URLで見せられる状態」を作る |
| データ | 再起動・再デプロイ・15分アクセスなしのスリープで **DBと写真が消える** 前提で使う |
| バックアップ | 商品を登録・承認したら、管理画面「データ連携」でJSONをエクスポートして手元に保管 |
| 復元 | 消えたら承認者が「バックアップから復元」。**公開URL（slug）と公開状態がそのまま戻る** ので、印刷済みのQRも使える |
| 写真 | 復元の対象外（JSONに含めない）。消えたら再アップロード |
| サンプル | `SEED_SAMPLE_DATA=true` なら、DBが空のとき起動時にサンプル2件を自動投入（いつでもデモできる） |
| 初回アクセス | スリープ明けは表示まで約1分かかる。お客さんがQRを読み取る本番運用には向かない |
| 有料化の目安 | 実際の商品にQRを貼って配るタイミング。`render.yaml` の `plan: starter` ＋ディスクに切り替える |

| 利用者 | 使う画面・機能 | 認証方法 |
|---|---|---|
| お客さん | 物語ページ | なし（公開ページ） |
| 編集者 | 商品の登録・編集・承認申請 | ユーザー名＋パスワード（Cookie セッション） |
| 承認者 | 上記＋承認・差し戻し・削除・監査ログ | 同上 |
| 他システム | REST API | APIトークン（`Authorization: Bearer ...`） |

---

## 2. アーキテクチャ（3層構造）

```mermaid
flowchart TB
    req["HTTPリクエスト"] --> routes
    subgraph routes["routes/（ルート層・Controller）"]
        direction LR
        r1["admin.py<br/>画面"]
        r2["api.py<br/>JSON"]
        r3["public.py<br/>物語ページ"]
        r4["auth.py<br/>ログイン・CSRF"]
    end
    routes --> services
    subgraph services["services/（サービス層・業務ルール）"]
        direction LR
        s1["product_service"]
        s2["workflow_service"]
        s3["audit_service"]
        s4["transfer_service"]
        s5["auth_service / permissions"]
    end
    services --> repos
    subgraph repos["repositories/（リポジトリ層・SQL）"]
        direction LR
        p1["products"]
        p2["approvals"]
        p3["audit_logs"]
        p4["users"]
    end
    repos --> sqlite[("SQLite")]
```

| 層 | フォルダ | やること | やらないこと | FastAPI の案件だと |
|---|---|---|---|---|
| ルート層 | `routes/` | リクエストから値を取り出す／サービスを呼ぶ／HTML・JSONで返す | 業務ルール、SQL | `routers/`（`APIRouter`）、`schemas/`（Pydantic） |
| サービス層 | `services/` | 権限チェック、状態チェック、入力チェック、**トランザクション**、監査ログ | 画面の都合（flash、HTML） | `services/`（呼び方はチームによって `usecases/`、`domain/` など） |
| リポジトリ層 | `repositories/` | SQL を実行する | commit、業務ルール | `repositories/` や `crud/`（SQLAlchemy） |

フォルダ名はプロジェクトごとに違いますが、Python の Web アプリでも「受け口・業務ルール・DBアクセスを分ける」考え方は共通です。

**なぜ分けるのか**：画面（`admin.py`）と API（`api.py`）が **同じサービスの関数** を呼ぶので、
「画面からはできないのに API からはできてしまう」といったルールのズレが起きません。

### 1つのリクエストの流れ（例：承認ボタン）

```mermaid
sequenceDiagram
    actor U as 承認者
    participant R as routes/admin.py
    participant W as workflow_service
    participant P as permissions
    participant DB as repositories → SQLite

    U->>R: POST /admin/products/3/workflow/approve
    R->>W: perform(user, 3, "approve", comment)
    W->>P: require(user, "product.approve")
    P-->>W: OK（無ければ ForbiddenError → 403）
    W->>DB: 商品と審査中の申請を取得
    W->>W: 状態チェック（pending か？）<br/>職務分掌チェック（申請者 ≠ 自分？）
    Note over W,DB: ここから1トランザクション
    W->>DB: 申請を approved に
    W->>DB: 商品を published に（version+1）
    W->>DB: 監査ログを INSERT
    Note over W,DB: commit（途中で失敗したら全部 rollback）
    W-->>R: "published"
    R-->>U: 302 → 詳細画面（「承認を行いました」）
```

---

## 3. DB設計

### ER図

```mermaid
erDiagram
    users ||--o{ products : "作成する"
    users ||--o{ approval_requests : "申請する / 審査する"
    products ||--o{ timeline_events : "持つ"
    products ||--o{ scans : "読み取られる"
    products ||--o{ approval_requests : "申請される"

    users {
        INTEGER id PK
        TEXT username UK
        TEXT display_name
        TEXT password_hash
        TEXT role "editor / approver"
        TEXT api_token_hash UK
        TEXT created_at
    }
    products {
        INTEGER id PK
        TEXT slug UK "公開URL用ランダム文字列"
        TEXT name
        TEXT producer_name
        TEXT origin
        TEXT catchcopy
        TEXT story
        TEXT producer_message
        TEXT image_filename
        TEXT status "draft / pending / published / rejected"
        INTEGER version "楽観的ロック"
        INTEGER created_by FK
        TEXT created_at
        TEXT updated_at
    }
    timeline_events {
        INTEGER id PK
        INTEGER product_id FK
        TEXT event_date
        TEXT title
        TEXT description
    }
    scans {
        INTEGER id PK
        INTEGER product_id FK
        TEXT scanned_at
        TEXT user_agent
    }
    approval_requests {
        INTEGER id PK
        INTEGER product_id FK
        TEXT status "pending / approved / rejected / withdrawn"
        INTEGER requested_by FK
        TEXT requested_at
        TEXT request_comment
        TEXT snapshot_json "申請時点の内容"
        INTEGER reviewed_by FK
        TEXT reviewed_at
        TEXT review_comment
    }
    audit_logs {
        INTEGER id PK
        TEXT occurred_at
        INTEGER actor_user_id "FKなし（あえて）"
        TEXT actor_name
        TEXT action
        TEXT target_type
        INTEGER target_id
        TEXT before_json
        TEXT after_json
        TEXT via "web / api"
        TEXT ip_address
    }
```

監査ログ（`audit_logs`）だけは、どの表とも線（外部キー）でつないでいません。理由は [6. 監査ログ設計](#6-監査ログ設計) で説明します。

### テーブル定義（主なもの）

**products（商品）**

| 列 | 型 | 必須 | 説明 |
|---|---|---|---|
| id | INTEGER | ○ | 主キー（自動採番） |
| slug | TEXT | ○ | 公開URL `/p/<slug>`。連番だと他商品のURLを推測されるのでランダム。UNIQUE |
| name | TEXT | ○ | 商品名（100文字以内） |
| producer_name | TEXT | ○ | 生産者名（100文字以内） |
| origin / catchcopy / story / producer_message | TEXT |  | 産地・キャッチコピー（60）・物語（5000）・ひとこと（1000） |
| status | TEXT | ○ | 状態。CHECK 制約で4種類以外は保存できない |
| version | INTEGER | ○ | 更新のたびに +1。同時編集の検知に使う |
| created_by | INTEGER |  | 作成者（users.id） |

**approval_requests（申請）** — 申請1回につき1行。差し戻し→再申請すると行が増えるので、履歴がすべて残ります。

| 列 | 説明 |
|---|---|
| status | `pending`（審査中）→ `approved` / `rejected` / `withdrawn` のどれかで確定 |
| snapshot_json | **申請した瞬間の商品内容（JSON）**。「承認者は何を見て承認したのか」を後から証明できる |
| requested_by / reviewed_by | 申請者・審査者。職務分掌のチェックに使う |

### 設計で意識したこと

| 考え方 | このアプリでの例 |
|---|---|
| 制約は DB にも書く | `CHECK (status IN (...))`、`UNIQUE`、`NOT NULL`。アプリにバグがあっても変なデータが入らない最後の砦 |
| 1対多は子に外部キー | `timeline_events.product_id → products.id` |
| 親を消したら子も消す | `ON DELETE CASCADE`（商品を消すと記録・読み取りログも消える） |
| 消してはいけないものはつながない | 監査ログは外部キーなし |
| 検索条件にはインデックス | `status`、`(target_type, target_id)` など |
| 日時は文字列（ISO形式）で統一 | `2026-10-28 23:59:00`。SQLite には日時型がないため |

---

## 4. 申請・承認フロー（状態遷移）

### 状態遷移図

```mermaid
stateDiagram-v2
    [*] --> draft : 登録（編集者）
    draft --> pending : submit 申請
    pending --> draft : withdraw 取り下げ
    pending --> published : approve 承認（申請者以外の承認者）
    pending --> rejected : reject 差し戻し（理由必須）
    rejected --> pending : submit 再申請
    published --> draft : unpublish 公開停止（承認者）

    draft : 下書き（編集できる）
    pending : 承認待ち（編集できない）
    published : 公開中（QRから見られる）
    rejected : 差し戻し（編集できる）
```

### 状態遷移表（`services/workflow_service.py` の `TRANSITIONS`）

| 操作 | できる状態 | 次の状態 | 必要な権限 | 追加ルール |
|---|---|---|---|---|
| submit（申請） | draft, rejected | pending | product.submit | 物語の本文が必要。申請時点の内容をスナップショット保存 |
| withdraw（取り下げ） | pending | draft | product.withdraw | |
| approve（承認） | pending | published | product.approve | **申請者本人は不可（職務分掌）** |
| reject（差し戻し） | pending | rejected | product.reject | 申請者本人は不可。**理由（コメント）必須** |
| unpublish（公開停止） | published | draft | product.unpublish | |

- 表にない組み合わせはすべて **409 Conflict**（例：下書きをいきなり承認）
- 編集できるのは `draft` と `rejected` だけ（承認した内容が、承認後に勝手に変わらないように）

### 実装のポイント

```python
# if 文を並べる代わりに「表」で持つ
TRANSITIONS = {
    "submit":  {"from": {"draft", "rejected"}, "to": "pending",   "permission": "product.submit"},
    "approve": {"from": {"pending"},           "to": "published", "permission": "product.approve"},
    ...
}
```

状態や操作が増えても、この表に1行足すだけで済みます。設計書の表とコードが1対1で対応するので、レビューもしやすくなります。

---

## 5. 権限設計（ロール）

`services/permissions.py` の `ROLE_PERMISSIONS`（RBAC：ロールベースアクセス制御）

| 権限 | 編集者 editor | 承認者 approver |
|---|:---:|:---:|
| product.write（登録・編集・記録） | ○ | ○ |
| product.submit / withdraw（申請・取り下げ） | ○ | ○ |
| transfer.import（JSON取り込み） | ○ | ○ |
| product.approve / reject（承認・差し戻し） | | ○ |
| product.unpublish（公開停止） | | ○ |
| product.delete（削除） | | ○ |
| audit.read（監査ログ閲覧） | | ○ |
| transfer.restore（バックアップから復元） | | ○ |

- **画面でボタンを隠す** ＋ **サーバーでも必ずチェックする** の二重構え。
  ボタンを隠すだけだと、URL を直接叩かれたら通ってしまうため（「画面を信用しない」）。
- 認証（あなたは誰？）は `auth_service.py`、認可（それをしていい？）は `permissions.py` と分けています。

---

## 6. 監査ログ設計

### 何を記録するか

| action | いつ | before / after |
|---|---|---|
| auth.login / auth.login_failed | ログイン成功・失敗 | 失敗時は入力されたユーザー名 |
| auth.issue_token | APIトークン発行 | — |
| product.create | 登録 | after：登録内容 |
| product.update | 編集 | **変わった項目だけ** before / after |
| product.delete | 削除 | before：削除した内容まるごと |
| product.submit / approve / reject / withdraw / unpublish | 状態の変更 | status とコメント |
| timeline_event.create / delete | 記録の追加・削除 | 記録の内容 |
| transfer.export / transfer.import / transfer.restore | ファイル連携 | 件数・作成された id |

### 設計の原則

1. **業務データの変更と同じトランザクションで書く**
   → 「データは変わったのにログが無い」「ログはあるのにデータが変わっていない」が起きない
2. **追記のみ（Append-only）**
   → リポジトリに `insert` と `search` しか作っていない。画面にも削除ボタンは無い
3. **外部キーを付けない＆名前をコピーして持つ**
   → 商品やユーザーが削除されても、ログは残り「誰が」も読める
4. **差分だけ残す**
   → `audit_service.diff()` で変わった項目だけを抽出。読みやすく、容量も小さい
5. **経路（web / api）と IP を残す**
   → 「画面から操作されたのか、他システムから来たのか」を区別できる

---

## 7. REST API 設計

ベースURL：`/api/v1`　認証：`Authorization: Bearer <APIトークン>`　形式：JSON（UTF-8）

| メソッド | パス | 説明 | 成功時 |
|---|---|---|---|
| GET | `/me` | 自分の情報 | 200 |
| GET | `/products?status=` | 商品一覧 | 200 |
| POST | `/products` | 商品登録（下書き） | **201** ＋ `Location` ヘッダー |
| GET | `/products/{id}` | 商品詳細（記録つき） | 200 |
| PATCH | `/products/{id}` | 一部更新（`version` 必須） | 200 |
| DELETE | `/products/{id}` | 削除（承認者） | **204**（本文なし） |
| GET | `/products/{id}/events` | 記録一覧 | 200 |
| POST | `/products/{id}/events` | 記録追加 | 201 |
| DELETE | `/events/{id}` | 記録削除 | 204 |
| POST | `/products/{id}/submit` | 申請 | 200 |
| POST | `/products/{id}/withdraw` | 取り下げ | 200 |
| POST | `/products/{id}/approve` | 承認 | 200 |
| POST | `/products/{id}/reject` | 差し戻し（`comment` 必須） | 200 |
| POST | `/products/{id}/unpublish` | 公開停止 | 200 |
| GET | `/approval-requests?status=` | 申請一覧 | 200 |
| GET | `/audit-logs?action=&target_type=&target_id=&limit=` | 監査ログ（承認者） | 200 |
| GET | `/export` | JSONファイルでエクスポート | 200 |
| POST | `/import` | JSON取り込み（ボディ or `file`） | 201 |

### ステータスコードの使い分け

| コード | 意味 | このAPIで返す場面 |
|---|---|---|
| 200 OK | 成功 | 取得・更新・ワークフロー操作 |
| 201 Created | 作成した | 商品・記録の登録、インポート |
| 204 No Content | 成功・返すものなし | 削除 |
| 401 Unauthorized | 誰か分からない | トークンなし・間違い |
| 403 Forbidden | 誰かは分かるが権限なし | 編集者が承認、自分の申請を承認 |
| 404 Not Found | 対象がない | 存在しない id |
| 409 Conflict | 今の状態ではできない | 承認待ちを編集、古い version で更新 |
| 422 Unprocessable Entity | 入力が不正 | 必須項目なし、日付形式が違う |

### エラーの形（全APIで統一）

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "入力内容に誤りがあります。",
    "details": [
      { "field": "name", "message": "商品名は必須です。" },
      { "field": "catchcopy", "message": "60文字以内にしてください。" }
    ]
  }
}
```

`code` は機械が判定するため（英大文字固定）、`message` は人が読むため、`details` は入力欄ごとの表示に使います。
実装は `errors.py` の例外クラス → `routes/api.py` の `handle_app_error` で一括変換しています。

### 設計のコツ（このAPIで守っていること）

- URL は **名詞の複数形**（`/products`）。動詞は HTTP メソッドで表す
- 「承認」のような CRUD に当てはまらない操作は `POST /products/{id}/approve` の形にする
- バージョンを URL に入れる（`/v1`）。互換性のない変更は `/v2` を作る
- レスポンスは DB の行をそのまま返さず、`to_dict()` で「外に見せる形」に詰め替える
- 画面用の Cookie では API を使えない（トークン必須）。CSRF の心配をなくすため

---

## 8. ファイル連携（JSON）設計

### ファイル形式（`format_version: 1`）

```json
{
  "format": "story-qr-products",
  "format_version": 1,
  "exported_at": "2026-10-28T12:00:00",
  "mode": "create",
  "products": [
    {
      "slug": "k3Jx9aQw", "status": "published",
      "name": "…", "producer_name": "…", "origin": "…",
      "catchcopy": "…", "story": "…", "producer_message": "…",
      "events": [ { "event_date": "2026-04-28", "title": "…", "description": "…" } ]
    }
  ]
}
```

サンプル：[`docs/sample-import.json`](sample-import.json)

### 取り込みモード

| mode | 誰が | slug（公開URL） | status | 使いどころ |
|---|---|---|---|---|
| `create`（省略時） | 編集者・承認者 | 新しく作る（ファイルの値は無視） | 必ず `draft` | 他システム・Excel作業からの新規登録 |
| `restore` | **承認者のみ** | ファイルの値を引き継ぐ（重複は422） | ファイルの値（`pending` は `draft` に） | 無料プランでデータが消えたときのバックアップ復元 |

`restore` は承認フローを通さずに「公開中」に戻せるので、承認者だけに権限（`transfer.restore`）を与えています。

### 取り込み処理の流れ

```mermaid
flowchart TD
    A["ファイル受信"] --> B{"JSONとして読める？"}
    B -- いいえ --> X["422：読み込めない"]
    B -- はい --> C{"format / format_version は正しい？"}
    C -- いいえ --> Y["422：想定外のファイル"]
    C -- はい --> D["全件チェック<br/>（ここでは1件も登録しない）"]
    D --> E{"エラーが1件でもある？"}
    E -- はい --> Z["422：全エラーを<br/>products[2].name のような場所つきで返す"]
    E -- いいえ --> F["1トランザクションで全件登録<br/>create：status は必ず draft・slug は新規<br/>restore：status と slug をファイルのまま"]
    F --> G["監査ログ transfer.import / transfer.restore"]
    G --> H["201：作成件数と id"]
```

### ファイル連携の設計ポイント

| ポイント | 理由 |
|---|---|
| ヘッダー情報（`format`, `format_version`）を持たせる | 違うファイルを間違って取り込む事故を防ぐ。形式を変えるときは version を上げる |
| **オール・オア・ナッシング** | 途中まで登録されると、どこから再送すればいいか分からなくなる |
| エラーは「全部」「場所つき」で返す | 1件直して再送→また別のエラー、の往復を減らす |
| 取り込んだデータは下書き | 外部データをそのまま公開せず、承認フローを必ず通す（復元だけは例外で、承認者に限定） |
| 件数上限（500件） | 巨大ファイルでサーバーが固まるのを防ぐ |
| エクスポート・インポートも監査ログに残す | 「いつ、誰が、何件やり取りしたか」を追える |

---

## 9. 排他制御（楽観的ロック）

**問題**：A さんと B さんが同じ商品の編集画面を開き、A さんが保存 → B さんが保存すると、A さんの変更が消える（「後勝ち」問題）。

**対策**：`products.version` を使う。

```sql
UPDATE products SET ..., version = version + 1
WHERE id = :id AND version = :画面を開いたときのversion
```

- 更新できた行数が **0** なら、誰かが先に更新している → **409 Conflict** を返す
- 画面ではフォームに `<input type="hidden" name="version">` を入れて送る
- API では `PATCH` のボディに `"version": 3` を入れてもらう

「ロック（鍵）をかけずに、保存のときにぶつかっていないか確かめる」ので **楽観的** ロックと呼びます。
業務システムではとてもよく使われる方式です。

---

## 10. セキュリティ対策

| 脅威 | 対策 | 場所 |
|---|---|---|
| SQLインジェクション | SQL に値を直接つなげず `?` / `:name` のプレースホルダを使う | `repositories/` 全体 |
| XSS（スクリプト埋め込み） | Jinja2 の自動エスケープ。`| safe` を使わない。改行は CSS で表示 | `templates/` |
| CSRF（なりすまし送信） | フォームに合言葉トークンを入れて POST ごとに照合 | `routes/auth.py` |
| パスワード漏えい | ハッシュ化して保存（werkzeug） | `auth_service.py` |
| APIトークン漏えい | SHA-256 ハッシュで保存、表示は発行時の1回だけ、再発行で旧トークン無効 | `auth_service.py` |
| セッション固定攻撃 | ログイン時に `session.clear()` | `routes/auth.py` |
| オープンリダイレクト | `next` は自サイト内（`/` 始まり）のみ許可 | `routes/auth.py` |
| 推測可能なURL | 公開URLはランダム文字列（slug） | `product_service._new_slug` |
| 危険なファイル名 | 保存時にランダムな名前を付け直す。拡張子を制限。5MB 上限 | `product_service.save_image` |
| 秘密情報のコミット | 環境変数で設定。本番で初期値のままなら起動しない | `config.py` / `app.py` |
| Cookie の盗聴 | 本番は `Secure`・`HttpOnly`・`SameSite=Lax` | `config.py` |

---

## 11. 次の案件との対応表（Python）

次の案件も Python なので、このアプリのコードは **書き方の参考としてそのまま読めます**。
ただし Web フレームワークは違う可能性が高いです。このアプリは画面もある Flask、
API 中心の新規開発では **FastAPI** がよく選ばれます（案件で決まっているものがあれば、そちらに読み替えてください）。

### 11-1. 担当業務 → このコードのどこを読むか

| 案件で担当すること | このアプリの場所 | ここで学べること |
|---|---|---|
| **JSONベース通信** | `routes/api.py` の `json_body()`、`product_service.to_dict()` | JSONを受け取って辞書にする／DBの行をそのまま返さず「外に見せる形」に詰め替える |
| **REST API開発** | `routes/api.py`（全体）、`errors.py`、`api.http` | URL・メソッド・ステータスコードの決め方、エラー形式の統一、`201 + Location`、`204` |
| **DB設計** | `schema.sql`、本書3章 | 主キー・外部キー・UNIQUE・CHECK・インデックス、1対多、履歴を残す表、消さない表 |
| **システム間のファイル授受** | `services/transfer_service.py`、`product_service.save_image()`、本書8章 | 形式チェック、全件チェック→全件登録、場所つきエラー、ファイル名の付け直し、サイズ・拡張子制限 |
| **申請・承認フロー** | `services/workflow_service.py`、本書4章 | 状態遷移表、職務分掌、差し戻し理由必須、申請時スナップショット |
| **監査機能** | `services/audit_service.py`、`repositories/audit_logs.py`、本書6章 | 同一トランザクションで記録、追記のみ、差分だけ残す、経路とIP |
| （共通）認証・認可 | `services/auth_service.py`、`services/permissions.py`、`routes/api.py` の `authenticate()` | トークン認証、ロールと権限の表、「画面を信用しない」 |
| （共通）トランザクション・排他制御 | `db.py` の `transaction()`、`repositories/products.py` の `update()` | commit / rollback の範囲、楽観的ロックで409 |
| （共通）テスト | `tests/test_app.py` | 正常系だけでなく 401 / 403 / 409 / 422 を確かめる書き方 |

### 11-2. Flask（このアプリ）→ FastAPI の読み替え表

| やりたいこと | このアプリ（Flask） | FastAPI だと |
|---|---|---|
| URL と関数を結びつける | `bp = Blueprint("api", ...)`、`@bp.get("/products")` | `router = APIRouter()`、`@router.get("/products")` |
| JSON を受け取って入力チェック | `json_body()` ＋ `validate_product()` を自分で書く | **Pydantic** の `BaseModel` を引数に書くだけ。型・必須・文字数を自動チェックし、ダメなら自動で 422 |
| 返す JSON の形を決める | `to_dict()` で詰め替え | `response_model=ProductOut`（Pydantic）で宣言 |
| 業務エラー → HTTPエラー | `AppError` ＋ `@bp.errorhandler` | `HTTPException` を raise、または `@app.exception_handler(AppError)` |
| APIトークンで「誰か」を確認 | `@bp.before_request` の `authenticate()` | `Depends(get_current_user)`（`HTTPBearer` / `OAuth2PasswordBearer`） |
| 権限チェック | サービス内で `permissions.require()` | 同じくサービス内、または `Depends(require_permission("product.approve"))` |
| DB接続 | `sqlite3` ＋ `get_db()` | **SQLAlchemy** の `Session` を `Depends(get_session)` で受け取る |
| トランザクション | `with transaction():` | `with session.begin():` |
| 表の定義・変更 | `schema.sql` を直接書く | SQLAlchemy のモデル（クラス）＋ **Alembic** のマイグレーション |
| ファイルを受け取る | `request.files["file"]` | `file: UploadFile` |
| ファイルを返す | `send_file()` / `Response(...)` | `FileResponse` / `StreamingResponse` |
| API を試す | `api.http`（REST Client） | 起動するだけで `/docs` に **Swagger UI** が自動で出る（OpenAPI 仕様書も自動生成） |
| テスト | `app.test_client()` ＋ unittest | `TestClient(app)` ＋ pytest |

**ポイント**：FastAPI では「入力チェック」と「API仕様書」を Pydantic とフレームワークがかなり肩代わりしてくれます。
その分、自分で考える必要があるのは **サービス層の業務ルール**（状態遷移、職務分掌、監査ログ、トランザクション）で、
そこはこのアプリの `services/` の書き方がほぼそのまま使えます。

### 11-3. 「ファイル授受」をこのアプリで分解すると

このアプリのファイル連携は「商品データのJSON」ですが、案件のファイル授受は **任意のファイル（CSV・PDF・ZIPなど）を送る・受け取る** ものになるはずです。流れを分解すると、ほとんどの部品はこのアプリにあります。

```mermaid
flowchart LR
    A["① 受付<br/>アップロード"] --> B["② 検証<br/>形式・サイズ・中身"]
    B --> C["③ 保存<br/>ファイル本体＋情報をDBへ"]
    C --> D["④ 申請・承認<br/>送ってよいか"]
    D --> E["⑤ 受け渡し<br/>相手がダウンロード／連携"]
    A & B & C & D & E --> F["⑥ 監査ログ<br/>全段階を記録"]
```

| 段階 | このアプリの該当箇所 | 案件で追加・置き換えになりそうなもの |
|---|---|---|
| ① 受付 | `routes/admin.py` の `transfer_import`、`routes/api.py` の `import_products`（multipart 受信） | 大きいファイルの分割アップロード、受付番号の発行 |
| ② 検証 | `save_image()`（拡張子・5MB上限・ファイル名付け直し）、`import_products()`（形式・全件チェック） | ウイルスチェック、ハッシュ値（改ざん検知）、CSVの列チェック |
| ③ 保存 | `static` ではなく `UPLOAD_FOLDER` に保存、DBにはファイル名だけ持つ | ファイル本体はクラウドのストレージ（Azure なら Blob Storage）、DB には「ファイル情報の表」（名前・サイズ・ハッシュ・保存場所・状態） |
| ④ 申請・承認 | `workflow_service.py`（状態遷移表・職務分掌） | 承認の多段化（担当→上長）、承認期限 |
| ⑤ 受け渡し | エクスポート（`Content-Disposition: attachment`） | 有効期限つきのダウンロードURL、相手システムへのAPI送信・再送 |
| ⑥ 監査 | `audit_service.record()` を同じトランザクションで | 「誰がダウンロードしたか」まで記録、ログの長期保管 |

> 金融系では特に ②（検証）と ⑥（監査）が厳しく求められることが多いので、
> `transfer_service.py` と `audit_service.py` はじっくり読んでおくのがおすすめです。

### 11-4. 読む順番のおすすめ（案件準備として）

1. **本書 3章（DB設計）と `schema.sql`** … 表の作り方と「なぜその制約か」
2. **本書 7章と `routes/api.py`** … `api.http` で実際に叩きながら、ステータスコードとエラー形式を確認
3. **`services/workflow_service.py`** … 状態遷移表と `perform()` の「①存在 ②権限 ③状態 ④トランザクション」の順番
4. **`services/audit_service.py` と `db.py`** … ログをどのトランザクションで書くか
5. **`services/transfer_service.py`** … ファイル授受のチェックの順番
6. **`tests/test_app.py`** … 仕様がテスト名になっているので、仕様の確認として読む

### 11-5. 実務の案件では、ここがさらに発展します

- **承認の多段化**：担当者承認 → 上長承認 のように段階が増える（`approval_steps` のような表を作り、順番を持たせる）
- **代理承認・承認者の自動決定**：組織マスタから承認ルートを決める
- **変更申請**：公開中の内容を残したまま「変更案」だけを申請する（このアプリでは一度下書きに戻す簡易版）
- **ファイル連携の非同期化**：大きいファイルは受付だけして裏で処理し、結果を後から取得する
- **監査ログの改ざん防止**：ハッシュチェーン、別の保管先への転送、保存期間の管理
- **認証の外部化**：自前のパスワード管理ではなく、会社のID基盤（Azure なら Entra ID）でログインする
