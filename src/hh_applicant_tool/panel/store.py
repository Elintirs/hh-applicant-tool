"""Состояние панели: расписание, история запусков, кэш статистики.

Хранится в panel.json рядом с config.toml. Конфиг утилиты сюда не попадает,
чтобы панель не мешала CLI и не портила комментарии в config.toml.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

PANEL_FILENAME = "panel.json"


class PanelStore:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.path = config_dir / PANEL_FILENAME
        self.jobs_dir = config_dir / "panel-jobs"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._read()

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Битый файл не должен ронять панель: начинаем с чистого листа,
            # старый файл оставляем рядом для разбора
            try:
                self.path.replace(self.path.with_suffix(".broken.json"))
            except OSError:
                pass
            return {}

    def _write(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._data.get(key, default)
            # Наружу отдаём копию, чтобы никто не правил состояние в обход
            # блокировки
            return json.loads(json.dumps(value))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._write()

    def update(self, key: str, fn: Callable[[Any], Any], default: Any = None):
        """Атомарно меняет значение функцией и возвращает результат."""
        with self._lock:
            value = fn(self._data.get(key, default))
            self._data[key] = value
            self._write()
            return value
