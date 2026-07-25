"""Журнал обращений к модели: что спросили и что она ответила.

Пишется построчным JSON (JSONL) рядом с конфигом. В отличие от log.txt здесь
только диалог с моделью и в структурированном виде — панель показывает его на
странице «Что делает модель». Файл самоограничивается по размеру.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__package__)

MAX_BYTES = 5 * 1024 * 1024
KEEP_LINES = 2000

_lock = threading.Lock()
_path: Path | None = None


def configure(path: str | Path | None) -> None:
    """Куда писать журнал. None — выключить."""
    global _path
    _path = Path(path) if path else None


def get_path() -> Path | None:
    return _path


def _trim(p: Path) -> None:
    """Оставляем хвост: журнал вспомогательный, старое не жалко."""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        p.write_text(
            "\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8"
        )
    except OSError as ex:
        logger.debug("Не удалось подрезать журнал AI: %s", ex)


def record(**fields: Any) -> None:
    """Дописывает одну запись. Никогда не бросает — это лишь наблюдение."""
    if _path is None:
        return
    fields.setdefault("ts", time.time())
    try:
        line = json.dumps(fields, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return
    try:
        with _lock:
            _path.parent.mkdir(parents=True, exist_ok=True)
            if _path.exists() and _path.stat().st_size > MAX_BYTES:
                _trim(_path)
            with _path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError as ex:
        logger.debug("Не удалось записать журнал AI: %s", ex)


def read_last(limit: int = 200) -> list[dict]:
    """Последние записи, новые первыми."""
    if _path is None or not _path.exists():
        return []
    try:
        lines = _path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def clear() -> None:
    if _path is not None and _path.exists():
        try:
            _path.unlink()
        except OSError as ex:
            logger.debug("Не удалось очистить журнал AI: %s", ex)
