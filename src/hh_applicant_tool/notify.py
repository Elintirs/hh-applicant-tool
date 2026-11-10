"""Уведомления в Telegram: что ждёт ответа человека, сбои задач, сводка.

Настройки в config.toml:

    [telegram]
    bot_token = "123:ABC"   # от @BotFather
    chat_id = "12345678"    # куда слать: личка с ботом

Без токена или chat_id отправка молча пропускается. Адрес запроса к API
содержит токен, поэтому в лог пишем только тип ошибки, не её текст.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
SENT_FILE = "telegram-sent.json"
SENT_KEEP = 500  # сколько последних ключей помнить, чтобы не слать повторно
TOKEN_RE = re.compile(r"^\d{5,}:[\w-]{20,}$")


def settings(config: Any) -> tuple[str, str]:
    section = (config.get("telegram") if config else None) or {}
    return str(section.get("bot_token") or "").strip(), str(section.get("chat_id") or "").strip()


def _call(token: str, method: str, payload: dict | None = None, timeout: float = 15) -> dict:
    try:
        r = requests.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
        data = r.json()
    except (requests.RequestException, ValueError) as ex:
        # В тексте исключения есть адрес с токеном: наружу только тип
        raise RuntimeError(f"Telegram недоступен ({type(ex).__name__})") from None
    if not data.get("ok"):
        raise RuntimeError(f"Telegram отказал: {data.get('description') or r.status_code}")
    return data


def _sent_keys(state_dir: Path) -> list[str]:
    try:
        return json.loads((state_dir / SENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def send(config: Any, text: str, *, key: str | None = None, state_dir: Path | None = None) -> bool:
    """Шлёт text в настроенный чат. key + state_dir: не слать одно и то же
    событие дважды (чат «ответить самому» висит непрочитанным много запусков).
    Ошибки не пробрасывает: уведомление не должно ронять задачу."""
    token, chat_id = settings(config)
    if not token or not chat_id:
        return False
    if key and state_dir and key in _sent_keys(state_dir):
        return False
    try:
        _call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
    except RuntimeError as ex:
        logger.warning("Не отправил уведомление: %s", ex)
        return False
    if key and state_dir:
        keys = [k for k in _sent_keys(state_dir) if k != key] + [key]
        try:
            (state_dir / SENT_FILE).write_text(json.dumps(keys[-SENT_KEEP:]), encoding="utf-8")
        except OSError:
            pass
    return True


def test(token: str, chat_id: str) -> None:
    """Пробное сообщение. Бросает RuntimeError с понятной причиной."""
    _call(token, "sendMessage", {"chat_id": chat_id, "text": "✅ Панель hh подключена: сюда будут приходить уведомления."})


def find_chats(token: str) -> list[dict[str, str]]:
    """Чаты, из которых недавно писали боту: так узнаём chat_id без ручного
    поиска. Пользователь сначала отправляет боту любое сообщение."""
    data = _call(token, "getUpdates", {"limit": 50})
    chats: dict[str, dict[str, str]] = {}
    for upd in data.get("result") or []:
        msg = upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username") or ""
            chats[str(chat["id"])] = {"id": str(chat["id"]), "name": name, "type": chat.get("type") or ""}
    return list(chats.values())


def upsert_section(text: str, values: dict[str, str]) -> str:
    """Заменяет или дописывает секцию [telegram] в тексте config.toml, не
    трогая остальное: обычное сохранение панели стирает комментарии."""
    lines = text.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        head = line.strip()
        if head.startswith("[") and head.endswith("]"):
            skip = head == "[telegram]"
            if skip:
                continue
        if not skip:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    body = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in values.items() if v]
    if body:
        out += ["", "[telegram]", "# Уведомления: ответить самому, сбои задач, вечерняя сводка", *body]
    return "\n".join(out) + "\n"
