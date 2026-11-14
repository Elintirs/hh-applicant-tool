import time

from hh_applicant_tool import chat_kind as ck
from hh_applicant_tool.chat_store import ChatStore


def msg(text, mine=False, author="Анна", at="2026-11-10T10:00:00", is_bot=False):
    return {"id": hash(text + at), "text": text, "mine": mine, "author": None if mine else author, "is_bot": is_bot, "at": at}


def test_classify_human_bot_ack():
    human = [msg("Добрый день! Когда удобно созвониться по вакансии?")]
    assert ck.classify(human)[0] == ck.HUMAN
    assert ck.classify([msg("Укажите зарплату", author="Робот-рекрутер")])[0] == ck.BOT
    assert ck.classify([msg("Здравствуйте! Я ассистент рекрутера на базе AI. Ваш опыт?", author="Анастасия")])[0] == ck.BOT
    ack = [msg("Иван, здравствуйте! Рассмотрим ваше резюме. Если навыки подойдут, мы свяжемся с вами.")]
    assert ck.classify(ack)[0] == ck.UNCLEAR
    # Отвечает за секунды дважды: автоматика, даже под именем
    fast = [msg("a", mine=True, at="2026-11-10T10:00:00"), msg("Ваш опыт?", at="2026-11-10T10:00:20"),
            msg("b", mine=True, at="2026-11-10T10:05:00"), msg("Зарплата?", at="2026-11-10T10:05:15")]
    assert ck.classify(fast)[0] == ck.BOT
    # Человек подключился после бота: смотрим на последнего автора
    mixed = [msg("Укажите опыт", author="ИИ-помощник", is_bot=True), msg("Иван, приглашаю на созвон в четверг?", author="Ольга")]
    assert ck.classify(mixed)[0] == ck.HUMAN


def test_template_across_chats(tmp_path):
    s = ChatStore(tmp_path)
    text = "Здравствуйте! Спасибо за отклик, пройдите короткое интервью с нашим рекрутером по ссылке ниже"
    for cid in (1, 2, 3):
        s.put_messages(cid, {"messages": [msg(text, author=f"HR {cid}")]})
    s.put_items([{"id": 3, "last_at": "2026-11-10T10:00:00", "state": "RESPONSE", "last_is_mine": False}])
    item = s.items()[0]
    assert item["kind"] == ck.UNCLEAR and "шаблон" in item["kind_reasons"][0]


def test_items_kind_and_cleanup(tmp_path):
    s = ChatStore(tmp_path)
    s.put_items([
        {"id": 1, "last_at": "2026-11-10T10:00:00", "state": "INVITATION", "last_is_mine": False,
         "_last_message": msg("Приглашаем на собеседование, удобно в 15:00?", author="Ольга")},
        {"id": 2, "last_at": "2020-01-01T10:00:00", "state": "RESPONSE", "last_is_mine": False},
    ])
    s.put_messages(2, {"messages": [msg("x", at="2020-01-01T10:00:00")]})
    items = {i["id"]: i for i in s.items()}
    assert items[1]["kind"] == ck.HUMAN and "_last_message" not in items[1]
    assert items[1]["score"] > items[2]["score"]
    assert s.messages(2) is not None
    assert s.cleanup(30) == 1
    assert [i["id"] for i in s.items()] == [1] and s.messages(2) is None
