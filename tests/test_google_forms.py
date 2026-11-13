import json

from hh_applicant_tool import google_forms as gf


def _html(items, settings=None):
    # Структура как в реальном FB_PUBLIC_LOAD_DATA_
    d1 = [None] * 11
    d1[1] = items
    d1[8] = "Тестовая анкета"
    d1[10] = settings or [None] * 7
    data = [None, d1, None, "Тест"]
    return (
        '<html><script>var FB_PUBLIC_LOAD_DATA_ = '
        + json.dumps(data, ensure_ascii=False)
        + ';</script><input type="hidden" name="fbzx" value="-123">'
    )


ITEMS = [
    # [id, title, descr, type, [[entry_id, options, required]]]
    [1, "ФИО", None, 1, [[11, None, 1]]],
    [2, "Город", None, 0, [[12, None, 1]]],
    [3, "Опыт Node.js", None, 2, [[13, [["Нет"], ["1-3 года"], ["Более 6 лет"]], 1]]],
    [4, "Стек", None, 4, [[14, [["React"], ["Vue"], ["Node.js"]], 0]]],
    [5, "Картинка", None, 11, [[None, None, 0]]],  # пропускаем
]
FINAL = "https://docs.google.com/forms/d/e/ABC/viewform"


def test_find_form_url():
    assert gf.find_form_url("анкета: https://forms.gle/AbC123, спасибо.") == "https://forms.gle/AbC123"
    assert gf.find_form_url("нет ссылки") is None


def test_parse_form():
    form = gf.parse_form(_html(ITEMS), FINAL)
    assert form.response_url == "https://docs.google.com/forms/d/e/ABC/formResponse"
    assert form.fbzx == "-123"
    assert [q.entry_id for q in form.questions] == [11, 12, 13, 14]  # картинка пропущена
    q = {x.title: x for x in form.questions}
    assert q["Опыт Node.js"].type == gf.RADIO and q["Опыт Node.js"].options[-1] == "Более 6 лет"
    assert q["Стек"].multiple is True


def test_parse_login_required():
    import pytest
    with pytest.raises(gf.FormError):
        gf.parse_form("<html>no data</html>", "https://accounts.google.com/signin")


def test_needs_login_flag():
    settings = [None, None, None, None, None, None, 1]  # [6] = 1
    assert gf.parse_form(_html(ITEMS, settings), FINAL).needs_login is True


def test_build_payload():
    form = gf.parse_form(_html(ITEMS), FINAL)
    q = {x.entry_id: x for x in form.questions}
    answers = [
        (q[11], "Иван Иванов"),
        (q[12], ""),  # пустое не отправляем
        (q[13], "Более 6 лет"),
        (q[14], ["React", "Node.js"]),
    ]
    p = gf.build_payload(answers)
    assert p["entry.11"] == "Иван Иванов"
    assert "entry.12" not in p
    assert p["entry.14"] == ["React", "Node.js"]
    assert p["fvv"] == "1" and p["pageHistory"] == "0"


def test_contact_answer_from_profile():
    p = {"first_name": "Никита", "last_name": "Иванов", "phone": "79990001122", "email": "a@b.c"}
    assert gf.contact_answer("Укажите: ФИО, Номер телефона, Telegram-логин", p) == "Иванов Никита, +79990001122"
    assert gf.contact_answer("Ваш e-mail", p) == "a@b.c"
    # Данных нет: пусто, заполнит пользователь
    assert gf.contact_answer("Номер телефона", {"first_name": "Никита"}) == ""
    # Не контакты: решает модель
    assert gf.contact_answer("Есть ли опыт работы с почтовыми сервисами?", p) is None
    assert gf.contact_answer("Сколько лет вы работаете разработчиком?", p) is None
