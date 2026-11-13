"""Кэш чатов в SQLite: панель показывает список и переписку сразу, а hh догружает в фоне.

Пишут в базу и панель, и задача reply-chats (разные процессы), поэтому режим WAL.
Старые чаты чистятся по дате последнего сообщения (chat_cache_days в [general]).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import chat_kind

logger = logging.getLogger(__package__)

FILENAME = "chats.db"
DEFAULT_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    item TEXT NOT NULL,          -- строка списка, как её отдаёт панель
    last_at REAL,                -- время последнего сообщения, для порядка и чистки
    seen_at REAL NOT NULL        -- когда hh последний раз отдал этот чат
);
CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,          -- ответ chats/:id панели
    saved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kinds (
    chat_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    reasons TEXT NOT NULL,
    tkeys TEXT NOT NULL          -- шаблонные ключи сообщений работодателя, для поиска рассылок
);
"""


def _ts(at: Any) -> float | None:
    if not at:
        return None
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize_message(m: dict[str, Any], me: str) -> dict[str, Any]:
    """Сообщение hh в формате панели."""
    display = m.get("participantDisplay") or {}
    return {
        "id": m.get("id"),
        "text": m.get("text") or "",
        "mine": str(m.get("participantId") or "") == me,
        "author": display.get("name"),
        "is_bot": bool(display.get("isBot")),
        "at": m.get("creationTime"),
    }


class ChatStore:
    def __init__(self, config_dir: Path | str):
        self.path = Path(config_dir) / FILENAME
        self._local = threading.local()
        with self._db() as db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        # Своё соединение на поток: панель отвечает из нескольких потоков
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            self._local.db = db
        return db

    # ------------------------------------------------------------ список
    def put_items(self, items: list[dict[str, Any]]) -> None:
        now = time.time()
        with self._db() as db:
            db.executemany(
                "INSERT INTO chats(id, item, last_at, seen_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET item=excluded.item, last_at=excluded.last_at, seen_at=excluded.seen_at",
                [(int(i["id"]), json.dumps(i, ensure_ascii=False), _ts(i.get("last_at")), now) for i in items],
            )

    def items(self, unread: bool = False) -> list[dict[str, Any]]:
        rows = self._db().execute("SELECT item FROM chats ORDER BY last_at DESC").fetchall()
        out = [self.with_kind(json.loads(r[0])) for r in rows]
        return [i for i in out if i.get("unread")] if unread else out

    def drop(self, chat_id: int) -> None:
        with self._db() as db:
            for table, col in (("chats", "id"), ("messages", "chat_id"), ("kinds", "chat_id")):
                db.execute(f"DELETE FROM {table} WHERE {col} = ?", (chat_id,))

    # ------------------------------------------------------------ переписка
    def put_messages(self, chat_id: int, data: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO messages(chat_id, data, saved_at) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data, saved_at=excluded.saved_at",
                (chat_id, json.dumps(data, ensure_ascii=False), time.time()),
            )
        self._update_kind(chat_id, data.get("messages") or [])

    def messages(self, chat_id: int) -> dict[str, Any] | None:
        row = self._db().execute("SELECT data FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # ------------------------------------------------------------ метки
    def _template_counts(self, exclude: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        for (keys,) in self._db().execute("SELECT tkeys FROM kinds WHERE chat_id != ?", (exclude,)):
            for k in json.loads(keys):
                counts[k] = counts.get(k, 0) + 1
        return counts

    def _update_kind(self, chat_id: int, messages: list[dict[str, Any]]) -> None:
        keys = sorted({chat_kind.template_key(m["text"]) for m in messages
                       if not m.get("mine") and (m.get("text") or "").strip()})
        counts = self._template_counts(chat_id)
        for k in keys:  # этот чат тоже считается
            counts[k] = counts.get(k, 0) + 1
        kind, reasons = chat_kind.classify(messages, counts)
        with self._db() as db:
            db.execute(
                "INSERT INTO kinds(chat_id, kind, reasons, tkeys) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET kind=excluded.kind, reasons=excluded.reasons, tkeys=excluded.tkeys",
                (chat_id, kind, json.dumps(reasons, ensure_ascii=False), json.dumps(keys, ensure_ascii=False)),
            )

    def with_kind(self, item: dict[str, Any]) -> dict[str, Any]:
        """Добавляет к строке списка метку. Переписки ещё нет: судим по последнему сообщению."""
        row = self._db().execute("SELECT kind, reasons FROM kinds WHERE chat_id = ?", (int(item["id"]),)).fetchone()
        if row:
            kind, reasons = row[0], json.loads(row[1])
        else:
            last = item.get("_last_message")
            kind, reasons = chat_kind.classify([last] if last else [])
        item = {k: v for k, v in item.items() if k != "_last_message"}
        return {**item, "kind": kind, "kind_reasons": reasons,
                "score": chat_kind.score(kind, item.get("state") or "", bool(item.get("last_is_mine")))}

    # ------------------------------------------------------------ чистка
    def cleanup(self, days: int = DEFAULT_DAYS) -> int:
        """Удаляет чаты без сообщений дольше days дней. Возвращает, сколько удалено."""
        edge = time.time() - days * 86400
        db = self._db()
        ids = [r[0] for r in db.execute(
            "SELECT id FROM chats WHERE COALESCE(last_at, seen_at) < ?", (edge,))]
        # Переписка без строки списка (сохранил reply-chats) тоже стареет
        ids += [r[0] for r in db.execute(
            "SELECT chat_id FROM messages WHERE saved_at < ? AND chat_id NOT IN (SELECT id FROM chats)", (edge,))]
        for cid in set(ids):
            self.drop(cid)
        if ids:
            with db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return len(set(ids))
