"""
errors.py — アプリ共通の「業務エラー」

サービス層で問題を見つけたら、これらの例外を raise します。
受け取る側（routes/）が、
    ・画面なら → flash メッセージにして表示
    ・APIなら  → HTTPステータスコード＋JSONにして返す
と変換します。こうすると「チェックの処理」は1か所に書くだけで、画面とAPIの両方で使えます。

    例外クラス         HTTPステータス  意味
    ValidationError   422            入力値がおかしい
    UnauthorizedError 401            ログインしていない／トークンが無効
    ForbiddenError    403            ログインはしているが、その操作の権限がない
    NotFoundError     404            対象が存在しない
    ConflictError     409            今の状態ではその操作ができない（例：承認待ちの商品を編集）
"""


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        # details：項目ごとのエラー [{"field": "name", "message": "必須です"}, ...]
        self.details = details or []

    def to_dict(self):
        """API で返す JSON の形。全APIでこの形に統一する。"""
        body = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
