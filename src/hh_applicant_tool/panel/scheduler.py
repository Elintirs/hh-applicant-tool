"""Расписание запусков.

Апстрим убрал cron, поэтому расписание живёт в панели: пока контейнер
с панелью запущен, задачи ставятся в общую очередь в нужное время.

Задача:
    {
      "id": "...", "name": "Поднять резюме", "args": ["update-resumes"],
      "enabled": true,
      "mode": "daily" | "interval" | "resume",
      "times": ["09:00", "18:30"],   # для daily
      "days": [0, 1, 2, 3, 4],       # 0 = понедельник, пусто = каждый день
      "every_minutes": 240,          # для interval
      # resume: запуск, как только hh разрешит поднять резюме (next_publish_at)
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
from typing import Any, Callable

from .runner import Job, JobManager
from .store import PanelStore

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
MISSED_GRACE = timedelta(minutes=10)
MIN_INTERVAL_MINUTES = 5
# Модель грузим в видеокарту заранее и выгружаем, если впереди пусто
AI_COMMANDS = {"apply-vacancies", "apply", "reply-chats", "respond-chats"}
WARMUP_BEFORE = timedelta(minutes=15)
UNLOAD_IF_IDLE = timedelta(hours=2)
RESUME_RETRY = timedelta(minutes=15)
RESUME_SLACK = timedelta(seconds=30)
# Запуск в режиме resume ушёл, ждём его окончания, hh не спрашиваем
WAITING = datetime.max
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
    elif mode != "resume":
        raise ScheduleError("Неизвестный режим расписания")
    for key in ("last_run_at", "last_job_id", "last_status"):
        if key in item:
            out[key] = item[key]
    return out


def next_run(item: dict[str, Any], after: datetime) -> datetime | None:
    # resume планирует сам Scheduler по ответу hh
    if not item.get("enabled") or item["mode"] == "resume":
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
    def __init__(
        self,
        store: PanelStore,
        jobs: JobManager,
        resume_ready_at: Callable[[str], datetime | None] | None = None,
        model_hook: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.store = store
        self.jobs = jobs
        # profile -> когда hh разрешит поднять резюме (None: поднимать нечего)
        self.resume_ready_at = resume_ready_at
        # (profile, загрузить?) -> грузит или выгружает модели профиля
        self.model_hook = model_hook
        # id задачи -> на какой запуск уже прогрели модель
        self._warmed: dict[str, datetime] = {}
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
            if nr == WAITING:
                nr = None
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
                if item["mode"] == "resume":
                    self._tick_resume(item, now)
                    continue
                planned = self._planned.get(item["id"])
                if planned is None:
                    planned = next_run(item, now - MISSED_GRACE)
                    if planned is None:
                        continue
                    if planned < now - MISSED_GRACE:
                        planned = next_run(item, now)
                    # План сбросили (сохранение расписания, перезапуск панели), а этот
                    # слот уже отработал: берём следующий, иначе запуск повторится
                    last = item.get("last_run_at")
                    if planned and item["mode"] == "daily" and last and last >= planned.timestamp():
                        planned = next_run(item, planned + timedelta(minutes=1))
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
            self._warmup(items, now)

    def _warmup(self, items: list[dict[str, Any]], now: datetime) -> None:
        """За WARMUP_BEFORE до задачи с моделью грузим модель в видеокарту,
        чтобы запуск не ждал минуты загрузки с диска."""
        for item in items:
            if not (item.get("enabled") and item["args"][0] in AI_COMMANDS):
                continue
            planned = self._planned.get(item["id"])
            if not planned or planned == WAITING or self._warmed.get(item["id"]) == planned:
                continue
            if now < planned <= now + WARMUP_BEFORE:
                self._warmed[item["id"]] = planned
                self._model_async(item.get("profile") or ".", True)

    def _ai_soon(self, now: datetime) -> bool:
        return any(
            item.get("enabled")
            and item["args"][0] in AI_COMMANDS
            and (p := self._planned.get(item["id"])) not in (None, WAITING)
            and p <= now + UNLOAD_IF_IDLE
            for item in self.store.get("schedule", [])
        )

    def _model_async(self, profile: str, load: bool) -> None:
        if not self.model_hook:
            return

        def run() -> None:
            try:
                self.model_hook(profile, load)
                logger.info("Модель %s", "загружена" if load else "выгружена")
            except Exception as ex:  # noqa: BLE001
                logger.warning("Не удалось %s модель: %s", "загрузить" if load else "выгрузить", ex)

        threading.Thread(target=run, name="panel-model", daemon=True).start()

    def _tick_resume(self, item: dict[str, Any], now: datetime) -> None:
        planned = self._planned.get(item["id"])
        if planned is None:
            # hh спрашиваем только тут: при старте и после каждого запуска
            try:
                ready = self.resume_ready_at(item.get("profile") or ".")
            except Exception as ex:  # noqa: BLE001
                logger.warning("Не узнал, когда поднимать резюме: %s", ex)
                ready = None
            planned = ready + RESUME_SLACK if ready else now + RESUME_RETRY
            self._planned[item["id"]] = planned
        if planned > now:
            return
        busy = self.jobs.is_running(
            lambda j, i=item["id"]: j.source == f"schedule:{i}"
        )
        if not busy:
            self._fire(item)
            self._planned[item["id"]] = WAITING

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
        if job.args and job.args[0] in AI_COMMANDS:
            # Модель больше не нужна в ближайшие часы: освобождаем видеокарту
            with self._lock:
                idle = not self._ai_soon(datetime.now()) and not self.jobs.is_running(
                    lambda j: bool(j.args) and j.args[0] in AI_COMMANDS
                )
            if idle:
                self._model_async(job.profile or ".", False)
        if not job.source.startswith("schedule:"):
            return
        item_id = job.source.split(":", 1)[1]
        with self._lock:
            if self._planned.get(item_id) == WAITING:
                del self._planned[item_id]

        def mark(items):
            for i in items or []:
                if i["id"] == item_id and i.get("last_job_id") == job.id:
                    i["last_status"] = job.status
            return items

        self.store.update("schedule", mark, [])
