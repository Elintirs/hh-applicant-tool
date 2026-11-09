import json

from hh_applicant_tool.operations import apply_vacancies as av
from hh_applicant_tool.operations import reply_chats as rc


def make(tmp_path, reply):
    op = object.__new__(rc.Operation)
    op.tool = type("Tool", (), {"config_dir_path": tmp_path, "ai_enabled": True})()
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
