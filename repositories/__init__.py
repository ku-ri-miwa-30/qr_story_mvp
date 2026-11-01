"""
repositories/ — リポジトリ層：「SQLを書く場所」

ルール：
    ・SQL はこの層にだけ書く（他の層に SELECT や INSERT が散らばらないように）
    ・ここでは commit しない（いつ確定するかはサービス層が決める）
    ・業務ルール（「承認待ちは編集できない」など）は書かない。ただDBを読み書きするだけ

FastAPI の案件では、SQLAlchemy を使った repositories/ や crud/ にあたる層です。
"""
