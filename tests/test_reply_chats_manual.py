import json

from hh_applicant_tool.operations import apply_vacancies as av
from hh_applicant_tool.operations import reply_chats as rc


def make(tmp_path, reply):
    op = object.__new__(rc.Operation)
    op.tool = type("Tool", (), {"config_dir_path": tmp_path, "ai_enabled": True, "config": {}})()
    op.dry_run, op.calls = False, []
    op.chat_referer = lambda chat_id: f"https://hh.ru/chat/{chat_id}"
    op.get_ai_reply = lambda chat, allow_manual=False: reply
    op.is_farewell = lambda text: False
    for name in ("notify_chat_opened", "mark_chat_read", "start_chat_typing", "send_chat_message"):
        setattr(op, name, lambda *a, n=name, **k: op.calls.append(n))
    chat = rc.ProcessingChat(
        chat_id=7, resume_id="r", current_participant_id="me",
        vacancy={"name": "Node.js", "company": {"name": "Acme"}},
        applicant_state="", last_message={"id": 1, "text": "Когда удобно созвониться?"},
        last_is_mine=False, viewed_by_me=False,
    )
    return op, chat


def journal(tmp_path):
    return [json.loads(x) for x in (tmp_path / av.JOURNAL_FILENAME).read_text().splitlines()]


def test_manual_chat_untouched_and_logged(tmp_path):
    op, chat = make(tmp_path, rc.MANUAL_MARKER)
    op._process_chat(chat)
    assert op.calls == []  # не прочитан и не отвечен: кандидат увидит его сам
    [rec] = journal(tmp_path)
    assert rec["result"] == "chat_manual" and rec["employer"] == "Acme"


def test_ordinary_reply_sent_and_logged(tmp_path):
    op, chat = make(tmp_path, "Здравствуйте! Да, опыт с NestJS есть.")
    op._process_chat(chat)
    assert "send_chat_message" in op.calls and "mark_chat_read" in op.calls
    [rec] = journal(tmp_path)
    assert rec["result"] == "chat_sent" and rec["reply"].startswith("Здравствуйте")


def test_manual_by_keywords():
    for text, manual in [
        ("Пожалуйста, заполните анкету: https://forms.gle/x", True),
        ("Когда вам удобно созвониться?", True),
        ("Напишите в телеграм @hr", True),
        ("Приглашаем на собеседование", True),
        ("Ваше местонахождение РФ / РБ?", False),
        ("Какой у вас опыт с NestJS?", False),
    ]:
        assert bool(rc.MANUAL_RE.search(text)) is manual, text


def test_bot_thanks_skipped():
    assert rc.is_bot_thanks("Спасибо за ответы! Мы передали их работодателю.")
    assert rc.is_bot_thanks("Всё зафиксировал, спасибо. Передаю информацию работодателю. Если ваши ответы подойдут, с вами свяжутся.")
    assert rc.is_bot_thanks("Благодарю за ответы! Представитель работодателя ознакомится с вашим резюме.")
    # Вопрос, анкета или тестовое - это просьба, на неё реагируем
    assert not rc.is_bot_thanks("Спасибо! Передам работодателю. А какие у вас ожидания по зарплате?")
    assert not rc.is_bot_thanks("Спасибо за отклик! Мы рассмотрим резюме, заполните анкету: https://forms.gle/x")
    assert not rc.is_bot_thanks("Укажите, пожалуйста, ваш опыт с Node.js")


def _ai_op(tmp_path, monkeypatch, replies, messages=None, last_is_mine=False):
    op, chat = make(tmp_path, None)
    del op.get_ai_reply  # нужен настоящий метод класса
    chat.last_is_mine = last_is_mine
    chat.last_message = {"id": 1, "text": "Какая зарплата?"}
    op._store = None
    op.cache_messages = lambda *a: None
    op.get_reply_options = lambda m: []
    op.get_chat_placeholders = lambda c: {"resume_salary": "", "first_name": "Иван"}
    op.get_open_chat_messages = lambda cid: messages or [
        {"id": 1, "participantId": "hr", "text": "Какая зарплата?"}]
    op.format_message_history = lambda *a, **k: "история"
    op.user_prompt = op.bump_prompt = "%(first_name)s"
    op.reply_chat_ai = object()
    calls = iter(replies)
    monkeypatch.setattr(rc, "ai_call", lambda client, prompt, what: (next(calls), {}))
    return op, chat


def test_invented_salary_rewritten_or_handed_to_human(tmp_path, monkeypatch):
    op, chat = _ai_op(tmp_path, monkeypatch, ["От 120 000 рублей.", "Готов обсудить после деталей задач."])
    assert op.get_ai_reply(chat, allow_manual=True) == "Готов обсудить после деталей задач."
    op, chat = _ai_op(tmp_path, monkeypatch, ["От 120 000 рублей.", "Ну, 150 000."])
    assert op.get_ai_reply(chat, allow_manual=True) == rc.MANUAL_MARKER


def test_no_second_bump_without_answer(tmp_path, monkeypatch):
    mine = [{"id": 1, "participantId": "hr", "text": "Анкета"}, {"id": 2, "participantId": "me", "text": "Заполнил"},
            {"id": 3, "participantId": "me", "text": "Актуальна ли вакансия?"}]
    op, chat = _ai_op(tmp_path, monkeypatch, ["Ещё раз напоминаю"], messages=mine, last_is_mine=True)
    assert op.get_ai_reply(chat) is None
