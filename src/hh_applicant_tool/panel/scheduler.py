"""Расписание запусков.

Апстрим убрал cron, поэтому расписание живёт в панели: пока контейнер
с панелью запущен, задачи ставятся в общую очередь в нужное время.

Задача:
    {
      "id": "...", "name": "Поднять резюме", "args": ["update-resumes"],
      "enabled": true,
      "mode": "daily" | "interval",
      "times": ["09:00", "18:30"],   # для daily
      "days": [0, 1, 2, 3, 4],       # 0 = понедельник, пусто = каждый день
      "every_minutes": 240,          # для interval
      "last_run_at": ..., "last_job_id": ..., "last_status": ...
    }

Пропущенные запуски (панель была выключена) не догоняем: если время
прошло больше чем на MISSED_GRACE, просто ждём следующего.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from .runner import Job, JobManager
from .store import PanelStore

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
MISSED_GRACE = timedelta(minutes=10)
MIN_INTERVAL_MINUTES = 5
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    pass


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    """Проверяет задачу из формы и приводит к каноничному виду."""
    name = str(item.get("name") or "").strip()
    args = item.get("args")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise ScheduleError("Не указана команда")
    if args[0] in ("panel", "web", "webpanel"):
        raise ScheduleError("Панель нельзя запускать по расписанию")
    mode = item.get("mode") or "daily"
    out: dict[str, Any] = {
        "id": item.get("id") or uuid.uuid4().hex[:10],
        "profile": str(item.get("profile") or "."),
        "name": name or args[0],
        "args": args,
        "enabled": bool(item.get("enabled", True)),
        "mode": mode,
    }
    if mode == "daily":
        times = item.get("times") or []
        clean = []
        for t in times:
            m = TIME_RE.match(str(t).strip())
            if not m:
                raise ScheduleError(f"Неверное время: {t!r}. Нужно ЧЧ:ММ")
            clean.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        if not clean:
            raise ScheduleError("Добавьте хотя бы одно время запуска")
        out["times"] = sorted(set(clean))
        days = sorted({int(d) for d in item.get("days") or [] if 0 <= int(d) <= 6})
        out["days"] = days
    elif mode == "interval":
        try:
            every = int(item.get("every_minutes") or 0)
        except (TypeError, ValueError):
            every = 0
        if every < MIN_INTERVAL_MINUTES:
            raise ScheduleError(
                f"Интервал не меньше {MIN_INTERVAL_MINUTES} минут"
            )
        out["every_minutes"] = every
    else:
        raise ScheduleError("Неизвестный режим расписания")
    for key in ("last_run_at", "last_job_id", "last_status"):
        if key in item:
            out[key] = item[key]
    return out


def next_run(item: dict[str, Any], after: datetime) -> datetime | None:
    if not item.get("enabled"):
        return None
    if item["mode"] == "interval":
        last = item.get("last_run_at")
        every = timedelta(minutes=item["every_minutes"])
        if not last:
            return after
        return max(after, datetime.fromtimestamp(last) + every)
    days = set(item.get("days") or range(7))
    for day_offset in range(8):
        day = (after + timedelta(days=day_offset)).date()
        if day.weekday() not in days:
            continue
        for t in item["times"]:
            hh, mm = map(int, t.split(":"))
            candidate = datetime.combine(day, datetime.min.time()).replace(
                hour=hh, minute=mm
            )
            if candidate >= after:
                return candidate
    return None


class Scheduler:
    def __init__(self, store: PanelStore, jobs: JobManager) -> None:
        self.store = store
        self.jobs = jobs
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Для каждой задачи помним, на какое время она уже стоит в плане,
        # чтобы не запустить дважды за одну минуту
        self._planned: dict[str, datetime] = {}
        jobs.on_finish(self._on_job_finish)

    # ---- данные -------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        items = self.store.get("schedule", [])
        now = datetime.now()
        for item in items:
            nr = self._planned.get(item["id"]) or next_run(item, now)
            item["next_run_at"] = nr.timestamp() if nr and item.get("enabled") else None
            item["busy"] = self.jobs.is_running(
                lambda j, i=item["id"]: j.source == f"schedule:{i}"
            )
        return items

    def save(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        old = {i["id"]: i for i in self.store.get("schedule", [])}
        clean = []
        for raw in items:
            prev = old.get(raw.get("id") or "", {})
            merged = {**{k: prev[k] for k in ("last_run_at", "last_job_id", "last_status") if k in prev}, **raw}
            clean.append(normalize(merged))
        with self._lock:
            self.store.set("schedule", clean)
            self._planned.clear()
        return self.list()

    def run_now(self, item_id: str) -> Job | None:
        item = next((i for i in self.store.get("schedule", []) if i["id"] == item_id), None)
        if not item:
            return None
        return self._fire(item)

    # ---- цикл -----------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._loop, name="panel-scheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick(datetime.now())
            except Exception:  # noqa: BLE001
                logger.exception("scheduler tick failed")
            self._stop.wait(TICK_SECONDS)

    def _tick(self, now: datetime) -> None:
        with self._lock:
            items = self.store.get("schedule", [])
            for item in items:
                if not item.get("enabled"):
                    self._planned.pop(item["id"], None)
                    continue
                planned = self._planned.get(item["id"])
                if planned is None:
                    planned = next_run(item, now - MISSED_GRACE)
                    if planned is None:
                        continue
                    if planned < now - MISSED_GRACE:
                        planned = next_run(item, now)
                    self._planned[item["id"]] = planned
                if planned and planned <= now:
                    # Не плодим очередь: если прошлый запуск этой задачи ещё
                    # идёт, этот пропускаем
                    busy = self.jobs.is_running(
                        lambda j, i=item["id"]: j.source == f"schedule:{i}"
                    )
                    if busy:
                        logger.info("Пропуск %s: прошлый запуск ещё идёт", item["name"])
                    else:
                        self._fire(item)
                    after = max(now, planned) + timedelta(minutes=1)
                    if item["mode"] == "interval":
                        after = now + timedelta(minutes=item["every_minutes"])
                    self._planned[item["id"]] = next_run(
                        {**item, "last_run_at": None}, after
                    ) if item["mode"] == "daily" else after

    def _fire(self, item: dict[str, Any]) -> Job:
        job = self.jobs.submit(
            item["args"],
            source=f"schedule:{item['id']}",
            title=item["name"],
            profile=item.get("profile") or ".",
        )

        def mark(items):
            for i in items or []:
                if i["id"] == item["id"]:
                    i["last_run_at"] = time.time()
                    i["last_job_id"] = job.id
                    i["last_status"] = job.status
            return items

        self.store.update("schedule", mark, [])
        return job

    def _on_job_finish(self, job: Job) -> None:
        if not job.source.startswith("schedule:"):
            return
        item_id = job.source.split(":", 1)[1]

        def mark(items):
            for i in items or []:
                if i["id"] == item_id and i.get("last_job_id") == job.id:
                    i["last_status"] = job.status
            return items

        self.store.update("schedule", mark, [])
