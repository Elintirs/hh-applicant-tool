import tomllib

import requests

from hh_applicant_tool import notify
from hh_applicant_tool.operations.daily_summary import summarize

CFG = {"telegram": {"bot_token": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAA", "chat_id": "42"}}


def test_upsert_keeps_rest_and_replaces_section():
    text = "# мой комментарий\n[general]\nknown_skills = \"Kafka\"\n\n[telegram]\nbot_token = \"old\"\n\n[openai.default]\nmodel = \"x\"\n"
    new = notify.upsert_section(text, {"bot_token": "t", "chat_id": "42"})
    data = tomllib.loads(new)
    assert "# мой комментарий" in new
    assert data["telegram"] == {"bot_token": "t", "chat_id": "42"}
    assert data["openai"]["default"]["model"] == "x" and data["general"]["known_skills"] == "Kafka"
    assert new.count("[telegram]") == 1


def test_send_dedup_and_no_token_leak(tmp_path, monkeypatch):
    calls = []

    class R:
        status_code = 200
        def json(self): return {"ok": True}

    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append(kw["json"]) or R())
    assert notify.send(CFG, "привет", key="chat:1:5", state_dir=tmp_path) is True
    assert notify.send(CFG, "привет", key="chat:1:5", state_dir=tmp_path) is False  # второй раз не шлём
    assert len(calls) == 1 and calls[0]["chat_id"] == "42"
    assert notify.send({}, "без настроек") is False

    def boom(url, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(requests, "post", boom)
    try:
        notify.test(CFG["telegram"]["bot_token"], "42")
    except RuntimeError as ex:
        assert "AAAA" not in str(ex) and "ConnectionError" in str(ex)


def test_summary():
    recs = [
        {"ts": "2026-11-11T10:00:00", "result": "sent"},
        {"ts": "2026-11-11T10:01:00", "result": "sent"},
        {"ts": "2026-11-11T10:02:00", "result": "ai_rejected"},
        {"ts": "2026-11-11T12:00:00", "result": "chat_manual", "chat_id": 1, "employer": "Acme", "vacancy": "Node.js", "url": "https://hh.ru/chat/1"},
        {"ts": "2026-11-11T15:00:00", "result": "chat_manual", "chat_id": 1, "employer": "Acme", "vacancy": "Node.js", "url": "https://hh.ru/chat/1"},  # тот же чат в следующем запуске
        {"ts": "2026-11-10T10:00:00", "result": "sent"},  # вчера
    ]
    text = summarize(recs, "2026-11-11")
    assert "2 отправлено, 1 отсеял фильтр" in text and "1 ждут тебя" in text and "Acme" in text
    assert summarize(recs, "2026-11-12") is None
