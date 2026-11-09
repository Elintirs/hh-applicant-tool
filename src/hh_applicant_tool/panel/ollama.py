"""Локальные нейронки через Ollama.

Панель ходит в API Ollama (обычно сервис ollama в том же docker compose):
показывает, что установлено, скачивает модели с прогрессом, удаляет и
подставляет выбранную модель в секции [openai.*]. Сама утилита общается с
Ollama как с обычным OpenAI-совместимым сервером по адресу <url>/v1.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import requests

from ..utils.system import is_docker
from .store import PanelStore

# Модели, которые хорошо работают с утилитой. vision нужна для капчи
SUGGESTED = [
    {"name": "qwen3:8b", "size": "5,2 ГБ", "note": "Письма, фильтр, чаты. Нужно около 8 ГБ видеопамяти"},
    {"name": "qwen3-vl:8b", "size": "6,1 ГБ", "note": "То же плюс картинки: подходит для капчи", "vision": True},
    {"name": "qwen3:4b", "size": "2,5 ГБ", "note": "Если видеопамяти мало или нет видеокарты"},
    {"name": "gemma3:12b", "size": "8,1 ГБ", "note": "Сильнее в текстах, нужно 12 ГБ видеопамяти", "vision": True},
]


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, store: PanelStore) -> None:
        self.store = store
        self._pulls: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        saved = self.store.get("ollama_url")
        if saved:
            return saved.rstrip("/")
        env = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST")
        if env:
            return (env if env.startswith("http") else f"http://{env}").rstrip("/")
        # В docker compose Ollama доступна по имени сервиса
        return "http://ollama:11434" if is_docker() else "http://127.0.0.1:11434"

    def set_url(self, url: str) -> None:
        url = url.strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Адрес должен начинаться с http:// или https://")
        self.store.set("ollama_url", url or None)

    def _get(self, path: str, timeout: float = 5) -> Any:
        try:
            r = requests.get(self.url + path, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as ex:
            raise OllamaError(f"Ollama недоступна по адресу {self.url}: {ex}") from ex

    def keep(self, model: str, keep_alive: str | int) -> None:
        """Загрузить модель в память на keep_alive ("3h") или выгрузить (0).
        Загрузка большой модели с диска идёт минутами, отсюда долгий таймаут."""
        try:
            r = requests.post(
                self.url + "/api/generate",
                json={"model": model, "keep_alive": keep_alive},
                timeout=(10, 900),
            )
            r.raise_for_status()
        except requests.RequestException as ex:
            raise OllamaError(f"Ollama не загрузила {model}: {ex}") from ex

    def loaded(self) -> list[dict[str, Any]]:
        """Модели, которые сейчас в памяти Ollama (видеокарта и ОЗУ)."""
        return [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "size_vram": m.get("size_vram"),
                "expires_at": m.get("expires_at"),
            }
            for m in self._get("/api/ps", timeout=3).get("models") or []
        ]

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url, "openai_url": self.url + "/v1", "suggested": SUGGESTED}
        try:
            out["version"] = self._get("/api/version").get("version")
        except OllamaError as ex:
            out.update(ok=False, error=str(ex), models=[], pulls=self.pulls())
            return out
        tags = self._get("/api/tags").get("models") or []
        try:
            running = {m.get("name") for m in self._get("/api/ps").get("models") or []}
        except OllamaError:
            running = set()
        out["ok"] = True
        out["models"] = [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
                "family": (m.get("details") or {}).get("family"),
                "params": (m.get("details") or {}).get("parameter_size"),
                "quant": (m.get("details") or {}).get("quantization_level"),
                "loaded": m.get("name") in running,
            }
            for m in tags
        ]
        out["pulls"] = self.pulls()
        return out

    def pulls(self) -> list[dict[str, Any]]:
        with self._lock:
            # Завершённые загрузки показываем ещё минуту, потом убираем
            now = time.time()
            for k in [k for k, v in self._pulls.items() if v.get("done_at") and now - v["done_at"] > 60]:
                del self._pulls[k]
            return [dict(v) for v in self._pulls.values()]

    def pull(self, name: str) -> None:
        name = name.strip()
        if not name or any(c.isspace() for c in name):
            raise ValueError("Укажите имя модели, например qwen3:8b")
        with self._lock:
            cur = self._pulls.get(name)
            if cur and not cur.get("done_at"):
                raise RuntimeError("Эта модель уже скачивается")
            self._pulls[name] = {"name": name, "status": "Запрашиваем…", "completed": 0, "total": 0, "error": None, "done_at": None}
        threading.Thread(target=self._pull, args=(name,), daemon=True).start()

    def _pull(self, name: str) -> None:
        state = self._pulls[name]
        try:
            with requests.post(self.url + "/api/pull", json={"model": name, "stream": True}, stream=True, timeout=(10, 600)) as r:
                r.raise_for_status()
                # chunk_size=None: отдавать строки сразу, а не копить по 512 байт
                for line in r.iter_lines(chunk_size=None):
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise OllamaError(msg["error"])
                    with self._lock:
                        state["status"] = msg.get("status") or state["status"]
                        if msg.get("total"):
                            state["total"] = msg["total"]
                            state["completed"] = msg.get("completed") or 0
            with self._lock:
                state["status"] = "Готово"
        except (requests.RequestException, OllamaError, ValueError) as ex:
            with self._lock:
                state["error"] = str(ex)
                state["status"] = "Ошибка"
        finally:
            with self._lock:
                state["done_at"] = time.time()

    def delete(self, name: str) -> None:
        try:
            r = requests.delete(self.url + "/api/delete", json={"model": name}, timeout=30)
            r.raise_for_status()
        except requests.RequestException as ex:
            raise OllamaError(f"Не удалось удалить модель: {ex}") from ex
