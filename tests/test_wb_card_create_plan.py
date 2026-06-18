from seller_agent.tasks.wb_card_create_plan import _plain_text


def test_plain_text_keeps_line_breaks_between_html_blocks() -> None:
    value = "Заголовок<br/><br/>Текст<ul><li>Первое</li><li>Второе</li></ul>"

    assert _plain_text(value) == "Заголовок\nТекст\nПервое\nВторое"


def test_plain_text_removes_wb_forbidden_emoji_symbols() -> None:
    value = "<ul><li>⚡ Мгновенный доступ</li><li>🧵 Усиленные швы</li></ul>"

    assert _plain_text(value) == "Мгновенный доступ\nУсиленные швы"
