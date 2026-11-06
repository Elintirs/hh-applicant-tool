"""Запуск команд утилиты дочерними процессами.

Команды, которые ходят в hh.ru, выполняются по одной: апстрим не рассчитан
на параллельные сессии, а hh быстрее показывает капчу на пачку запросов.
Поэтому есть общая очередь. Исключение: команды с --loop работают часами,
их держать в очереди нельзя, они запускаются сразу.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store import PanelStore

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 200
# Сколько вывода держим в памяти на задачу. Полный вывод лежит в файле
MEMORY_OUTPUT_LIMIT = 512 * 1024
STOP_GRACE_SECONDS = 10

QUEUED = "queued"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
STOPPED = "stopped"
CANCELED = "canceled"
FINAL = {SUCCESS, FAILED, STOPPED, CANCELED}


@dataclass
class Job:
    id: str
    args: list[str]
    source: str
    title: str
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    # Сдвиг начала буфера в памяти относительно начала всего вывода
    _offset: int = 0
    _buf: str = ""
    _proc: subprocess.Popen | None = None
    _stop_requested: bool = False
    _cond: threading.Condition = field(default_factory=threading.Condition)

    @property
    def in_lane(self) -> bool:
        return "--loop" not in self.args

    @property
    def command(self) -> str:
        return shlex.join(["hh-applicant-tool", *self.args])

    @property
    def output_size(self) -> int:
        return self._offset + len(self._buf)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "args": self.args,
            "command": self.command,
            "source": self.source,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "output_size": self.output_size,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        job = cls(
            id=d["id"],
            args=list(d.get("args") or []),
            source=d.get("source", "manual"),
            title=d.get("title", ""),
            status=d.get("status", FAILED),
            created_at=d.get("created_at") or time.time(),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            exit_code=d.get("exit_code"),
        )
        # Задачи, которые выполнялись при выключении панели, считаем
        # прерванными
        if job.status not in FINAL:
            job.status = STOPPED if job.status == RUNNING else CANCELED
            job.finished_at = job.finished_at or time.time()
        return job


class JobManager:
    def __init__(self, store: PanelStore, config_dir: Path) -> None:
        self.store = store
        self.config_dir = config_dir
        self.jobs_dir = store.jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._lane_busy = False
        self._listeners: list[Any] = []
        for d in store.get("jobs", []):
            try:
                job = Job.from_dict(d)
            except (KeyError, TypeError):
                continue
            self._jobs[job.id] = job
        self._persist()

    # ---- публичное API ------------------------------------------------

    def on_finish(self, fn) -> None:
        """Подписка на завершение задачи (нужна планировщику)."""
        self._listeners.append(fn)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )
            result = []
            for j in jobs:
                d = j.to_dict()
                if j.status == QUEUED and j.id in self._queue:
                    d["queue_position"] = list(self._queue).index(j.id) + 1
                result.append(d)
            return result

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, args: list[str], *, source: str = "manual", title: str = "") -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            args=list(args),
            source=source,
            title=title or (args[0] if args else "команда"),
        )
        with self._lock:
            self._jobs[job.id] = job
            if job.in_lane:
                self._queue.append(job.id)
            self._trim_history()
            self._persist()
        if job.in_lane:
            self._pump()
        else:
            self._start(job)
        return job

    def stop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status == QUEUED:
                if job_id in self._queue:
                    self._queue.remove(job_id)
                self._finish(job, CANCELED, None)
                return True
            if job.status != RUNNING or not job._proc:
                return False
            job._stop_requested = True
            proc = job._proc
        # Сначала вежливо: утилита ловит SIGINT/SIGTERM и сохраняет куки
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return True

        def killer():
            try:
                proc.wait(STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()

        threading.Thread(target=killer, daemon=True).start()
        return True

    def send_input(self, job_id: str, text: str) -> bool:
        job = self.get(job_id)
        if not job or job.status != RUNNING or not job._proc or not job._proc.stdin:
            return False
        try:
            job._proc.stdin.write((text + "\n").encode())
            job._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        self._append(job, text + "\n", echo=True)
        return True

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in FINAL:
                return False
            del self._jobs[job_id]
            self._persist()
        try:
            self._log_path(job_id).unlink()
        except FileNotFoundError:
            pass
        return True

    def read_output(self, job: Job, offset: int) -> tuple[str, int]:
        """Возвращает вывод начиная с offset и новый offset."""
        with job._cond:
            if job.status not in FINAL and offset >= job._offset:
                if offset >= job.output_size:
                    return "", job.output_size
                return job._buf[offset - job._offset :], job.output_size
        # Нужного куска уже нет в памяти (или панель перезапускалась):
        # читаем файл
        try:
            data = self._log_path(job.id).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return "", offset
        return data[offset:], len(data)

    def wait_output(self, job: Job, offset: int, timeout: float) -> None:
        with job._cond:
            if job.output_size > offset or job.status in FINAL:
                return
            job._cond.wait(timeout)

    def is_running(self, predicate) -> bool:
        with self._lock:
            return any(
                j.status in (QUEUED, RUNNING) and predicate(j)
                for j in self._jobs.values()
            )

    def shutdown(self) -> None:
        with self._lock:
            running = [j for j in self._jobs.values() if j.status == RUNNING]
        for j in running:
            self.stop(j.id)

    # ---- внутреннее ----------------------------------------------------

    def _log_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.log"

    def _trim_history(self) -> None:
        finished = sorted(
            (j for j in self._jobs.values() if j.status in FINAL),
            key=lambda j: j.created_at,
        )
        excess = len(self._jobs) - HISTORY_LIMIT
        for j in finished[: max(0, excess)]:
            del self._jobs[j.id]
            try:
                self._log_path(j.id).unlink()
            except FileNotFoundError:
                pass

    def _persist(self) -> None:
        with self._lock:
            self.store.set("jobs", [j.to_dict() for j in self._jobs.values()])

    def _pump(self) -> None:
        with self._lock:
            if self._lane_busy or not self._queue:
                return
            job = self._jobs.get(self._queue.popleft())
            if not job:
                return
            self._lane_busy = True
        self._start(job)

    def _command(self, args: list[str]) -> list[str]:
        return [
            sys.executable,
            "-m",
            "hh_applicant_tool",
            *args,
        ]

    def _start(self, job: Job) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Путь передаём через окружение: флаг --config-dir в утилите
        # срабатывает слишком поздно (путь кэшируется при создании объекта).
        # Профиль уже входит в путь, поэтому HH_PROFILE_ID убираем
        env["CONFIG_DIR"] = str(self.config_dir)
        env.pop("HH_PROFILE_ID", None)
        try:
            proc = subprocess.Popen(
                self._command(job.args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                # Своя группа процессов, чтобы Ctrl+C панели не убивал задачи
                start_new_session=True,
            )
        except OSError as ex:
            self._append(job, f"Не удалось запустить: {ex}\n")
            self._finish(job, FAILED, None)
            return
        with self._lock:
            job._proc = proc
            job.status = RUNNING
            job.started_at = time.time()
            self._persist()
        self._append(job, f"$ {job.command}\n", echo=True)
        threading.Thread(target=self._reader, args=(job, proc), daemon=True).start()

    def _reader(self, job: Job, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        decoder_buf = b""
        while True:
            # Читаем кусками, а не строками: приглашения ввода (капча, код из
            # СМС) приходят без перевода строки
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            decoder_buf += chunk
            try:
                text = decoder_buf.decode("utf-8")
                decoder_buf = b""
            except UnicodeDecodeError as ex:
                # Обрезанный посреди символа хвост дочитаем в следующий раз
                text = decoder_buf[: ex.start].decode("utf-8", errors="replace")
                decoder_buf = decoder_buf[ex.start :]
                if len(decoder_buf) > 8:
                    text += decoder_buf.decode("utf-8", errors="replace")
                    decoder_buf = b""
            if text:
                self._append(job, text)
        code = proc.wait()
        if job._stop_requested:
            status = STOPPED
        else:
            status = SUCCESS if code == 0 else FAILED
        self._finish(job, status, code)

    def _append(self, job: Job, text: str, echo: bool = False) -> None:
        if echo:
            # Свои строки (команда, ввод пользователя) помечаем, чтобы фронт
            # мог их выделить
            text = "\x1b[2m" + text.rstrip("\n") + "\x1b[0m\n"
        with job._cond:
            job._buf += text
            if len(job._buf) > MEMORY_OUTPUT_LIMIT:
                cut = len(job._buf) - MEMORY_OUTPUT_LIMIT
                job._buf = job._buf[cut:]
                job._offset += cut
            try:
                with self._log_path(job.id).open("a", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass
            job._cond.notify_all()

    def _finish(self, job: Job, status: str, code: int | None) -> None:
        with self._lock:
            was_lane = job.in_lane and job.status == RUNNING
            job.status = status
            job.exit_code = code
            job.finished_at = time.time()
            job._proc = None
            if was_lane:
                self._lane_busy = False
            self._persist()
        with job._cond:
            # Буфер в памяти больше не нужен: завершённый вывод читаем из файла
            job._offset += len(job._buf)
            job._buf = ""
            job._cond.notify_all()
        for fn in self._listeners:
            try:
                fn(job)
            except Exception:  # noqa: BLE001
                logger.exception("listener failed")
        self._pump()
