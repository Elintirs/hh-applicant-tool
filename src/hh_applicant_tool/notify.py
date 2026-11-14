"""Уведомления в Telegram о чатах, где нужен ответ человека.

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
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
SENT_FILE = "telegram-sent.json"
SENT_KEEP = 500  # сколько последних ключей помнить, чтобы не слать повторно
RETRIES = 3
RETRY_DELAY = 3  # сек: соединение с Telegram иногда рвётся на TLS


def settings(config: Any) -> tuple[str, str]:
    section = (config.get("telegram") if config else None) or {}
    return str(section.get("bot_token") or "").strip(), str(
        section.get("chat_id") or ""
    ).strip()


def call(
    token: str,
    method: str,
    payload: dict | None = None,
    timeout: float = 15,
    proxies: dict | None = None,
) -> dict:
    url = API.format(token=token, method=method)
    for attempt in range(RETRIES):
        try:
            r = requests.post(
                url, json=payload or {}, timeout=timeout, proxies=proxies
            )
            data = r.json()
            break
        except requests.ConnectionError:
            if attempt == RETRIES - 1:
                raise RuntimeError(
                    "Telegram недоступен (ConnectionError)"
                ) from None
            time.sleep(RETRY_DELAY)
        except (requests.RequestException, ValueError) as ex:
            # В тексте исключения есть адрес с токеном: наружу только тип
            raise RuntimeError(
                f"Telegram недоступен ({type(ex).__name__})"
            ) from None
    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram отказал: {data.get('description') or r.status_code}"
        )
    return data


def _sent_keys(state_dir: Path) -> list[str]:
    try:
        return json.loads((state_dir / SENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def send(
    config: Any,
    text: str,
    *,
    key: str | None = None,
    state_dir: Path | None = None,
    proxies: dict | None = None,
) -> bool:
    """Шлёт text в настроенный чат. key + state_dir: не слать одно и то же
    событие дважды (чат «ответить самому» висит непрочитанным много запусков).
    Ошибки не пробрасывает: уведомление не должно ронять задачу."""
    token, chat_id = settings(config)
    if not token or not chat_id:
        return False
    if key and state_dir and key in _sent_keys(state_dir):
        return False
    try:
        call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:4000],
                "disable_web_page_preview": True,
            },
            proxies=proxies,
        )
    except RuntimeError as ex:
        logger.warning("Не отправил уведомление: %s", ex)
        return False
    if key and state_dir:
        keys = [k for k in _sent_keys(state_dir) if k != key] + [key]
        try:
            (state_dir / SENT_FILE).write_text(
                json.dumps(keys[-SENT_KEEP:]), encoding="utf-8"
            )
        except OSError as ex:
            logger.warning("Не запомнил отправленное уведомление: %s", ex)
    return True
