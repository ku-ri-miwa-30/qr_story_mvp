"""qr_service.py — QRコード画像（PNG）を作る"""

import io

import qrcode


def make_png(url):
    """URL を埋め込んだ QR コードの PNG をメモリ上に作って返す（BytesIO）。"""
    qr = qrcode.QRCode(
        # 誤り訂正レベル M = 約15%汚れても読める（ラベルは擦れやすいので L より強め）
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
