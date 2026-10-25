"""PR の作者が書けるテンプレートの、想定外の形や値で起きる例外の一覧。

計算の途中で止まったときに、トレースバックにせず「見積もれなかった」と扱うために使う
（セキュリティレビューの指摘2）。
"""

MALFORMED_INPUT_ERRORS: tuple[type[Exception], ...] = (
    ArithmeticError,
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
)
