-- =====================================================================
-- schema.sql — テーブル設計（DB設計）
-- ---------------------------------------------------------------------
-- 表は6つ。役割で3グループに分かれる：
--
--   【業務データ】  products / timeline_events / scans
--   【認証】        users
--   【ワークフロー・監査】 approval_requests / audit_logs
--
-- ER図や各列の説明は docs/design.md にもまとめています。
-- ※ 表の構成を変えたら、開発中は data/story_qr.db を削除して作り直してください。
-- =====================================================================


-- ---------------------------------------------------------------------
-- users：管理画面・APIを使う人
--   role（役割）で「できること」が変わる（権限は services/permissions.py）
--     editor   … 登録・編集・申請ができる人（例：生産者）
--     approver … 上記に加えて、承認・差し戻し・削除・監査ログ閲覧ができる人
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    -- パスワードはそのまま保存せず「ハッシュ値」で保存する（漏れても元に戻せない）
    password_hash   TEXT    NOT NULL,
    -- CHECK 制約：決められた値以外は保存できない（DB側の最後の砦）
    role            TEXT    NOT NULL CHECK (role IN ('editor', 'approver')),
    -- APIトークンもハッシュ値で保存。発行時に1回だけ画面に表示する
    api_token_hash  TEXT    UNIQUE,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);


-- ---------------------------------------------------------------------
-- products：商品
--   status（状態）が承認フローの中心。状態遷移は services/workflow_service.py
--     draft     下書き      … 編集できる
--     pending   承認待ち    … 編集できない（承認者の判断待ち）
--     published 公開中      … QRから見られる
--     rejected  差し戻し    … 編集して再申請できる
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 公開URL用のランダム文字列（/p/1 のような連番だと他の商品を推測されるため）
    slug             TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    producer_name    TEXT    NOT NULL,
    origin           TEXT,
    catchcopy        TEXT,
    story            TEXT,
    producer_message TEXT,
    image_filename   TEXT,
    status           TEXT    NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'pending', 'published', 'rejected')),
    -- 楽観的ロック用のバージョン番号。更新のたびに +1 する。
    -- 「AさんとBさんが同時に編集して、後から保存した人が前の人の変更を消してしまう」のを防ぐ
    version          INTEGER NOT NULL DEFAULT 1,
    created_by       INTEGER REFERENCES users(id),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);


-- ---------------------------------------------------------------------
-- timeline_events：商品ができるまでの記録（トレーサビリティ）。商品 1 : 多 記録
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS timeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    event_date  TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    title       TEXT    NOT NULL,
    description TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_product ON timeline_events(product_id);


-- ---------------------------------------------------------------------
-- scans：QRが読み取られた記録。商品 1 : 多 読み取り
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    scanned_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_product ON scans(product_id);


-- ---------------------------------------------------------------------
-- approval_requests：申請（1回の「承認お願いします」が1行）
--   商品 1 : 多 申請（差し戻し→再申請 のたびに行が増える＝履歴が残る）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    requested_by    INTEGER NOT NULL REFERENCES users(id),
    requested_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    request_comment TEXT,
    -- 申請した瞬間の商品内容を JSON で保存。
    -- 「承認者は何を見て承認したのか」を後から確認できるようにするため
    snapshot_json   TEXT    NOT NULL,
    reviewed_by     INTEGER REFERENCES users(id),
    reviewed_at     TEXT,
    review_comment  TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_product ON approval_requests(product_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_requests(status);


-- ---------------------------------------------------------------------
-- audit_logs：監査ログ（誰が・いつ・何を・どう変えたか）
--   ・追記専用。アプリには UPDATE / DELETE する処理を一切作らない
--   ・あえて外部キーを付けない：商品やユーザーが削除されてもログは残す必要があるため
--     （そのため actor_name に「その時点の名前」をコピーして持っておく）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    actor_user_id INTEGER,            -- 操作した人（ログイン失敗時などは NULL）
    actor_name    TEXT,               -- 操作した人の名前（その時点のもの）
    action        TEXT    NOT NULL,   -- 例: product.create / product.approve / auth.login
    target_type   TEXT,               -- 例: product / timeline_event / user
    target_id     INTEGER,
    before_json   TEXT,               -- 変更前の値（JSON）
    after_json    TEXT,               -- 変更後の値（JSON）
    via           TEXT,               -- web（画面）/ api
    ip_address    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_logs(occurred_at);
