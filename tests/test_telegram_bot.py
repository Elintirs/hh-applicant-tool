from types import SimpleNamespace

import pytest

from hh_applicant_tool import notify
from hh_applicant_tool.panel import telegram_bot as tb

OWNER = "42"


class Store(dict):
    def set(self, k, v):
        self[k] = v


def make(tmp_path):
    store = Store(schedule=[
        {"id": "a", "name": "Отклики", "args": ["apply-vacancies", "--ai-filter", "--max-responses=200"], "enabled": True, "mode": "daily", "times": ["10:00"]},
        {"id": "c", "name": "Чаты", "args": ["reply-chats", "--no-loop"], "enabled": True, "mode": "daily", "times": ["12:00"]},
        {"id": "r", "name": "Резюме", "args": ["update-resumes"], "enabled": True, "mode": "resume"},
    ])
    sent_hh = []
    service = SimpleNamespace(chat_send=lambda cid, text: sent_hh.append((cid, text)), chats=lambda: None, chat_ai_draft=lambda cid: "Черновик")
    submitted = []
    app = SimpleNamespace(
        store=store,
        profile=".",
        scheduler=SimpleNamespace(save=lambda items: store.set("schedule", items), list=lambda: store["schedule"]),
        jobs=SimpleNamespace(submit=lambda args, **kw: submitted.append(args) or SimpleNamespace(id="j1"), is_running=lambda p: False, list=lambda: []),
        ctx=lambda: SimpleNamespace(service=service, dir=tmp_path, settings=SimpleNamespace(path=tmp_path / "config.toml")),
    )
    return tb.TelegramBot(app), app, sent_hh, submitted


@pytest.fixture
def outbox(monkeypatch):
    out = []
    monkeypatch.setattr(notify, "send_message", lambda token, chat, text, buttons=None: out.append((chat, text, buttons)))
    monkeypatch.setattr(notify, "_call", lambda *a, **k: {"ok": True})
    return out


def msg(text, chat=OWNER, reply_to=None):
    m = {"chat": {"id": int(chat)}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"text": reply_to}
    return {"update_id": 1, "message": m}


def cb(data, chat=OWNER):
    return {"update_id": 2, "callback_query": {"id": "q", "data": data, "from": {"id": int(chat)}, "message": {"chat": {"id": int(chat)}}}}


def test_ignores_strangers(tmp_path, outbox):
    bot, *_ = make(tmp_path)
    bot._handle("t", OWNER, msg("/menu", chat="999"))
    bot._handle("t", OWNER, cb("m:pause", chat="999"))
    assert outbox == []


def test_menu_has_buttons(tmp_path, outbox):
    bot, *_ = make(tmp_path)
    bot._handle("t", OWNER, msg("/start"))
    assert outbox and outbox[0][2] and any("Пауза" in b[0] for row in outbox[0][2] for b in row)


def test_reply_sends_only_after_confirm(tmp_path, outbox):
    bot, _, sent_hh, _ = make(tmp_path)
    bot._handle("t", OWNER, msg("Удобно в четверг в 15:00", reply_to="🙋 Acme\nhttps://chatik.hh.ru/chat/777"))
    assert sent_hh == []  # пока только предпросмотр с кнопкой
    tok = next(v for row in outbox[-1][2] for t, v in row if v.startswith("s:"))[2:]
    bot._handle("t", OWNER, cb(f"s:{tok}"))
    assert sent_hh == [(777, "Удобно в четверг в 15:00")]
    bot._handle("t", OWNER, cb(f"s:{tok}"))  # повторное нажатие не шлёт второй раз
    assert len(sent_hh) == 1


def test_pause_resume_and_run(tmp_path, outbox):
    bot, app, _, submitted = make(tmp_path)
    bot._handle("t", OWNER, cb("m:pause"))
    state = {i["id"]: i["enabled"] for i in app.store["schedule"]}
    assert state == {"a": False, "c": False, "r": True}  # резюме не трогаем
    bot._handle("t", OWNER, cb("m:resume"))
    assert all(i["enabled"] for i in app.store["schedule"])
    bot._handle("t", OWNER, cb("r:apply"))
    assert submitted == []  # сначала подтверждение
    bot._handle("t", OWNER, cb("rk:apply"))
    assert submitted == [["apply-vacancies", "--ai-filter", "--max-responses=200"]]
