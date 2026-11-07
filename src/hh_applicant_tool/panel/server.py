"""HTTP-сервер панели на стандартной библиотеке.

Безопасность: панель запускает любые команды утилиты и видит ключи API,
поэтому по умолчанию слушает только 127.0.0.1. Если задан токен, без него
пускает только на страницу входа. Все изменяющие запросы требуют заголовок
X-Panel-Request, который браузер не отправит с чужого сайта без CORS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import re
import secrets
import shlex
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import requests

from ..ai.base import AIError
from ..api.errors import ApiError as HHApiError
from ..errors import OperationError
from ..utils.package import PACKAGE_NAME, get_package_version, parse_version
from .hh import CaptchaBroker, HHService, friendly_error
from .introspect import build_schema, operation_title
from .runner import FINAL, JobManager
from .scheduler import Scheduler, ScheduleError
from .settings import AI_TASK_KEYS, Settings, SettingsError
from .stats import Stats
from .store import PanelStore

logger = logging.getLogger(__name__)

STATIC_PACKAGE = "hh_applicant_tool.panel"
COOKIE_NAME = "hh_panel"
SSE_KEEPALIVE = 15.0
MAX_BODY = 4 * 1024 * 1024
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,40}$")
UPDATE_CHECK_TTL = 6 * 3600


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ProfileCtx:
    name: str
    dir: Path
    settings: Settings
    service: HHService
    stats: Stats


class PanelApp:
    def __init__(self, base_dir: Path, profile: str = ".", token: str | None = None) -> None:
        self.base_dir = base_dir
        self.token = token or None
        self.secret = secrets.token_bytes(32)
        self.store = PanelStore(base_dir)
        self.jobs = JobManager(self.store, base_dir)
        self.scheduler = Scheduler(self.store, self.jobs)
        self.broker = CaptchaBroker()
        self._schema: dict[str, Any] | None = None
        self._ctx: dict[str, ProfileCtx] = {}
        self._lock = threading.Lock()
        self.started_at = time.time()
        # Профиль из флага или окружения важнее сохранённого в панели
        if profile and profile != ".":
            self.store.set("profile", profile)
        elif not self.store.get("profile"):
            self.store.set("profile", ".")
        self.jobs.on_finish(self._after_job)

    def _after_job(self, job) -> None:
        # Вход и выход меняют token.json: сбросим кэш утилиты профиля
        if job.args and job.args[0] in ("authorize", "authenticate", "auth", "login", "logout", "exit"):
            self.ctx(job.profile).service.reset()

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is None:
            self._schema = build_schema()
        return self._schema

    # ---- профили -----------------------------------------------------

    @property
    def profile(self) -> str:
        return self.store.get("profile") or "."

    def profile_dir(self, name: str) -> Path:
        return (self.base_dir / name).resolve() if name != "." else self.base_dir

    def ctx(self, name: str | None = None) -> ProfileCtx:
        name = name or self.profile
        with self._lock:
            c = self._ctx.get(name)
            if c is None:
                d = self.profile_dir(name)
                service = HHService(self.base_dir, name, self.broker)
                c = ProfileCtx(name, d, Settings(d), service, Stats(self.store, service))
                self._ctx[name] = c
            return c

    def profiles(self) -> list[dict[str, Any]]:
        names = ["."]
        if self.base_dir.exists():
            for p in sorted(self.base_dir.iterdir()):
                if p.is_dir() and not p.name.startswith((".", "panel-")) and (
                    (p / "config.toml").exists() or (p / "token.json").exists() or (p / ".panel-profile").exists()
                ):
                    names.append(p.name)
        out = []
        for n in names:
            d = self.profile_dir(n)
            out.append({"name": n, "title": "Основной" if n == "." else n, "logged_in": (d / "token.json").exists(), "active": n == self.profile})
        return out

    def create_profile(self, name: str) -> None:
        if not PROFILE_RE.match(name) or name in (".", ".."):
            raise ApiError("Имя профиля: латиница, цифры, точка, дефис, подчёркивание")
        d = self.profile_dir(name)
        if d.exists():
            raise ApiError("Такой профиль уже есть")
        d.mkdir(parents=True)
        (d / ".panel-profile").write_text("")

    def set_profile(self, name: str) -> None:
        if name not in {p["name"] for p in self.profiles()}:
            raise ApiError("Профиль не найден", 404)
        self.store.set("profile", name)

    # ---- авторизация -------------------------------------------------

    def session_value(self) -> str:
        assert self.token
        return hmac.new(self.secret, self.token.encode(), hashlib.sha256).hexdigest()

    def check_token(self, value: str) -> bool:
        return bool(self.token) and hmac.compare_digest(value.encode(), self.token.encode())

    # ---- обновления ----------------------------------------------------

    def check_updates(self, force: bool = False) -> dict[str, Any]:
        cached = self.store.get("updates") or {}
        local = get_package_version()
        if not force and cached.get("checked_at", 0) > time.time() - UPDATE_CHECK_TTL and cached.get("local") == local:
            return cached
        try:
            r = requests.get(f"https://pypi.org/pypi/{PACKAGE_NAME}/json", timeout=10)
            r.raise_for_status()
            remote = r.json()["info"]["version"]
            data = {
                "local": local,
                "remote": remote,
                "available": parse_version(local) < parse_version(remote),
                "checked_at": time.time(),
            }
        except (requests.RequestException, ValueError, KeyError) as ex:
            data = {"local": local, "error": str(ex), "checked_at": time.time()}
        self.store.set("updates", data)
        return data


Route = tuple[str, re.Pattern[str], Callable[..., None]]


def make_handler(app: PanelApp) -> type[BaseHTTPRequestHandler]:
    static_root = resources.files(STATIC_PACKAGE) / "static"
    routes: list[Route] = []

    def route(method: str, pattern: str):
        rx = re.compile("^/api/" + re.sub(r":(\w+)", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn):
            routes.append((method, rx, fn))
            return fn

        return deco

    class Handler(BaseHTTPRequestHandler):
        server_version = "hh-panel"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Не пишем каждый запрос в log.txt утилиты: фронт опрашивает
            # сервер раз в несколько секунд и забил бы лог
            pass

        # ---- ответ ---------------------------------------------------

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, data: Any, status: int = 200, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _error(self, message: str, status: int = 400) -> None:
            # Тело запроса могло остаться непрочитанным: закрываем соединение,
            # иначе его остаток примут за следующий запрос
            self.close_connection = True
            self.json({"error": message}, status, extra={"Connection": "close"})

        def _read_raw(self) -> bytes:
            # Тело читаем всегда, даже если обработчику оно не нужно: иначе
            # его остаток на keep-alive соединении примут за новый запрос
            if getattr(self, "_raw", None) is None:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._raw = b""
                    raise ApiError("Слишком большой запрос", 413)
                self._raw = self.rfile.read(length) if length else b""
            return self._raw

        def body(self) -> Any:
            raw = self._read_raw()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError as ex:
                raise ApiError("Некорректный JSON") from ex

        def _authorized(self) -> bool:
            if not app.token:
                return True
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
            morsel = cookie.get(COOKIE_NAME)
            return bool(morsel) and hmac.compare_digest(morsel.value, app.session_value())

        # ---- маршрутизация ---------------------------------------------

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            url = urlsplit(self.path)
            path = url.path
            self.query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            self._raw = None
            try:
                if method not in ("GET", "HEAD"):
                    self._read_raw()
                if not path.startswith("/api/"):
                    if method not in ("GET", "HEAD"):
                        raise ApiError("Метод не поддерживается", 405)
                    self._static(path)
                    return
                if method not in ("GET", "HEAD") and self.headers.get("X-Panel-Request") != "1":
                    raise ApiError("Запрос отклонён", 403)
                if path == "/api/login" and method == "POST":
                    self._login()
                    return
                if path == "/api/session":
                    self.json({"auth_required": bool(app.token), "authorized": self._authorized()})
                    return
                if not self._authorized():
                    raise ApiError("Нужно войти", 401)
                m = "GET" if method == "HEAD" else method
                for rmethod, rx, fn in routes:
                    if rmethod != m:
                        continue
                    match = rx.match(path)
                    if match:
                        fn(self, **match.groupdict())
                        return
                raise ApiError("Не найдено", 404)
            except ApiError as ex:
                self._error(str(ex), ex.status)
            except (SettingsError, ScheduleError, ValueError) as ex:
                self._error(str(ex), 400)
            except (RuntimeError, OperationError) as ex:
                self._error(str(ex), 409)
            except HHApiError as ex:
                self._error(friendly_error(ex), 502)
            except AIError as ex:
                self._error(f"Модель не ответила. Проверьте раздел «Модели». {ex}", 502)
            except requests.RequestException as ex:
                self._error(f"Нет связи с hh.ru: {ex}", 502)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as ex:  # noqa: BLE001
                logger.exception("panel error")
                self._error(f"Внутренняя ошибка: {ex}", 500)

        def _static(self, path: str) -> None:
            if path in ("", "/") or "." not in path.rsplit("/", 1)[-1]:
                path = "/index.html"
            rel = [p for p in path.split("/") if p and p not in (".", "..")]
            node = static_root
            for part in rel:
                node = node / part
            if not node.is_file():
                raise ApiError("Не найдено", 404)
            ctype = mimetypes.guess_type(rel[-1])[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, node.read_bytes(), ctype)

        def _login(self) -> None:
            if not app.token:
                self.json({"ok": True})
                return
            body = self.body()
            if not app.check_token(str(body.get("token") or "")):
                time.sleep(1)
                raise ApiError("Неверный токен", 401)
            cookie = f"{COOKIE_NAME}={app.session_value()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
            self.json({"ok": True}, extra={"Set-Cookie": cookie})

        def stream_job(self, job_id: str, offset: int) -> None:
            job = app.jobs.get(job_id)
            if not job:
                raise ApiError("Задача не найдена", 404)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last_state = None
            last_ping = time.monotonic()
            while True:
                text, offset = app.jobs.read_output(job, offset)
                if text:
                    self._event("output", {"text": text, "offset": offset})
                state = (job.status, json.dumps(job.prompt, sort_keys=True))
                if state != last_state:
                    last_state = state
                    self._event("status", job.to_dict())
                if job.status in FINAL and not text:
                    text, offset = app.jobs.read_output(job, offset)
                    if not text:
                        self._event("end", {"offset": offset})
                        return
                    continue
                app.jobs.wait_output(job, offset, 1.0)
                if time.monotonic() - last_ping > SSE_KEEPALIVE:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.monotonic()

        def _event(self, name: str, data: Any) -> None:
            payload = json.dumps(data, ensure_ascii=False)
            self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
            self.wfile.flush()

    def page(h: Handler) -> int:
        try:
            return max(0, int(h.query.get("page") or 0))
        except ValueError:
            return 0

    # ---- общее ---------------------------------------------------------

    @route("GET", "info")
    def info(h: Handler) -> None:
        h.json({
            "version": get_package_version(),
            "config_dir": str(app.base_dir),
            "profile": app.profile,
            "auth_required": bool(app.token),
            "started_at": app.started_at,
        })

    @route("GET", "schema")
    def schema(h: Handler) -> None:
        h.json(app.schema)

    @route("GET", "updates")
    def updates(h: Handler) -> None:
        h.json(app.check_updates(force=h.query.get("force") == "1"))

    # ---- профили и аккаунт ---------------------------------------------

    @route("GET", "profiles")
    def profiles(h: Handler) -> None:
        h.json({"items": app.profiles(), "active": app.profile})

    @route("POST", "profiles")
    def profile_create(h: Handler) -> None:
        name = str(h.body().get("name") or "").strip()
        app.create_profile(name)
        app.set_profile(name)
        h.json({"items": app.profiles(), "active": app.profile}, 201)

    @route("PUT", "profiles/active")
    def profile_switch(h: Handler) -> None:
        app.set_profile(str(h.body().get("name") or "."))
        h.json({"items": app.profiles(), "active": app.profile})

    @route("GET", "profile")
    def profile(h: Handler) -> None:
        h.json(app.ctx().stats.profile())

    @route("POST", "account/login")
    def account_login(h: Handler) -> None:
        body = h.body()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username:
            raise ApiError("Укажите телефон или почту")
        ctx = app.ctx()
        ctx.service.reset()
        job = app.jobs.submit(
            ["authorize", username],
            source="login",
            title="Вход в hh.ru",
            profile=ctx.name,
            secret_args=[password] if password else None,
        )
        h.json(job.to_dict(), 201)

    @route("POST", "account/logout")
    def account_logout(h: Handler) -> None:
        ctx = app.ctx()
        ctx.service.reset()
        job = app.jobs.submit(["logout"], source="manual", title="Выход из аккаунта", profile=ctx.name)
        h.json(job.to_dict(), 201)

    @route("POST", "account/refresh")
    def account_refresh(h: Handler) -> None:
        app.ctx().service.reset()
        h.json({"ok": True})

    # ---- вопросы от задач и капча ---------------------------------------

    @route("GET", "prompts")
    def prompts(h: Handler) -> None:
        items = [{**p, "source": "job"} for p in app.jobs.prompts()]
        for c in app.broker.pending():
            items.append({"source": "panel", "kind": "captcha", "captcha_id": c["id"], "image_data": c["image"], "prompt": "Введите текст с картинки", "title": "Запрос панели к hh.ru"})
        h.json({"items": items})

    @route("POST", "prompts/answer")
    def prompt_answer(h: Handler) -> None:
        body = h.body()
        text = str(body.get("text") or "")
        if body.get("captcha_id"):
            ok = app.broker.answer(str(body["captcha_id"]), text)
        else:
            ok = app.jobs.send_input(str(body.get("job_id") or ""), text)
        if not ok:
            raise ApiError("Вопрос уже неактуален", 409)
        h.json({"ok": True})

    @route("GET", "jobs/:job_id/captcha")
    def job_captcha(h: Handler, job_id: str) -> None:
        job = app.jobs.get(job_id)
        path = app.jobs.captcha_path(job) if job else None
        if not path or not path.exists():
            raise ApiError("Картинки нет", 404)
        h._send(200, path.read_bytes(), "image/png")

    # ---- задачи --------------------------------------------------------

    @route("GET", "jobs")
    def jobs_list(h: Handler) -> None:
        h.json({"jobs": app.jobs.list()})

    @route("POST", "jobs")
    def jobs_create(h: Handler) -> None:
        body = h.body()
        args = body.get("args")
        if isinstance(args, str):
            try:
                args = shlex.split(args)
            except ValueError as ex:
                raise ApiError(f"Не удалось разобрать команду: {ex}") from ex
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            raise ApiError("Не указана команда")
        if args and args[0] == "hh-applicant-tool":
            args = args[1:]
        if any(a in ("panel", "web", "webpanel") for a in args[:3]):
            raise ApiError("Панель из панели запускать не нужно")
        job = app.jobs.submit(args, source="manual", title=str(body.get("title") or operation_title(args)), profile=app.profile)
        h.json(job.to_dict(), 201)

    @route("GET", "jobs/:job_id")
    def job_get(h: Handler, job_id: str) -> None:
        job = app.jobs.get(job_id)
        if not job:
            raise ApiError("Задача не найдена", 404)
        h.json(job.to_dict())

    @route("GET", "jobs/:job_id/stream")
    def job_stream(h: Handler, job_id: str) -> None:
        try:
            offset = max(0, int(h.query.get("offset") or 0))
        except ValueError:
            offset = 0
        h.stream_job(job_id, offset)

    @route("POST", "jobs/:job_id/stop")
    def job_stop(h: Handler, job_id: str) -> None:
        if not app.jobs.stop(job_id):
            raise ApiError("Задачу нельзя остановить", 409)
        h.json({"ok": True})

    @route("POST", "jobs/:job_id/input")
    def job_input(h: Handler, job_id: str) -> None:
        if not app.jobs.send_input(job_id, str(h.body().get("text") or "")):
            raise ApiError("Задача не ждёт ввода", 409)
        h.json({"ok": True})

    @route("DELETE", "jobs/:job_id")
    def job_delete(h: Handler, job_id: str) -> None:
        if not app.jobs.delete(job_id):
            raise ApiError("Удалить можно только завершённую задачу", 409)
        h.json({"ok": True})

    # ---- расписание ------------------------------------------------------

    @route("GET", "schedule")
    def schedule_list(h: Handler) -> None:
        h.json({"items": app.scheduler.list()})

    @route("PUT", "schedule")
    def schedule_save(h: Handler) -> None:
        items = h.body().get("items")
        if not isinstance(items, list):
            raise ApiError("Ожидается список задач")
        for i in items:
            if isinstance(i, dict) and not i.get("profile"):
                i["profile"] = app.profile
        h.json({"items": app.scheduler.save(items)})

    @route("POST", "schedule/:item_id/run")
    def schedule_run(h: Handler, item_id: str) -> None:
        job = app.scheduler.run_now(item_id)
        if not job:
            raise ApiError("Задача расписания не найдена", 404)
        h.json(job.to_dict(), 201)

    # ---- конфиг, модели, тексты ---------------------------------------------

    @route("GET", "config")
    def config_get(h: Handler) -> None:
        h.json(app.ctx().settings.read_raw())

    @route("PUT", "config")
    def config_put(h: Handler) -> None:
        ctx = app.ctx()
        ctx.settings.write_raw(str(h.body().get("text") or ""))
        ctx.service.reset()
        h.json(ctx.settings.read_raw())

    @route("POST", "config/validate")
    def config_validate(h: Handler) -> None:
        try:
            app.ctx().settings.validate_raw(str(h.body().get("text") or ""))
        except SettingsError as ex:
            h.json({"ok": False, "error": str(ex)})
            return
        h.json({"ok": True})

    @route("POST", "config/restore")
    def config_restore(h: Handler) -> None:
        ctx = app.ctx()
        ctx.settings.restore_backup()
        ctx.service.reset()
        h.json(ctx.settings.read_raw())

    @route("GET", "models")
    def models_get(h: Handler) -> None:
        h.json(app.ctx().settings.read_models())

    @route("PUT", "models")
    def models_put(h: Handler) -> None:
        sections = h.body().get("sections")
        if not isinstance(sections, dict):
            raise ApiError("Ожидаются секции моделей")
        ctx = app.ctx()
        data = ctx.settings.save_models(sections)
        ctx.service.reset()
        h.json(data)

    def task_body(h: Handler) -> tuple[str, dict]:
        body = h.body()
        key = body.get("key")
        if key not in AI_TASK_KEYS:
            raise ApiError("Неизвестная задача")
        return key, body.get("values") or {}

    @route("POST", "models/test")
    def models_test(h: Handler) -> None:
        key, values = task_body(h)
        image = None
        if key == "captcha":
            image = (resources.files(STATIC_PACKAGE) / "static" / "img" / "captcha-sample.png").read_bytes()
        h.json(app.ctx().settings.test_model(key, values, image))

    @route("POST", "models/remote")
    def models_remote(h: Handler) -> None:
        key, values = task_body(h)
        h.json({"models": app.ctx().settings.list_remote_models(key, values)})

    @route("GET", "letters")
    def letters_get(h: Handler) -> None:
        h.json(app.ctx().settings.read_letters())

    @route("PUT", "letters")
    def letters_put(h: Handler) -> None:
        h.json(app.ctx().settings.save_letters(h.body()))

    @route("POST", "letters/preview")
    def letters_preview(h: Handler) -> None:
        try:
            text = app.ctx().settings.preview(str(h.body().get("text") or ""))
        except SettingsError as ex:
            h.json({"ok": False, "error": str(ex)})
            return
        h.json({"ok": True, "text": text})

    @route("GET", "texts")
    def texts_get(h: Handler) -> None:
        h.json(app.ctx().settings.read_texts())

    @route("PUT", "texts")
    def texts_put(h: Handler) -> None:
        h.json(app.ctx().settings.save_texts(h.body()))

    @route("POST", "texts/preview")
    def texts_preview(h: Handler) -> None:
        try:
            text = app.ctx().settings.preview_loose(str(h.body().get("text") or ""))
        except SettingsError as ex:
            h.json({"ok": False, "error": str(ex)})
            return
        h.json({"ok": True, "text": text})

    @route("POST", "smtp/test")
    def smtp_test(h: Handler) -> None:
        app.ctx().settings.test_smtp(str(h.body().get("to") or ""))
        h.json({"ok": True})

    # ---- статистика --------------------------------------------------------

    @route("GET", "stats")
    def stats_get(h: Handler) -> None:
        h.json({"stats": app.ctx().stats.cached()})

    @route("POST", "stats/refresh")
    def stats_refresh(h: Handler) -> None:
        h.json({"stats": app.ctx().stats.refresh()})

    # ---- резюме ------------------------------------------------------------

    @route("GET", "resumes")
    def resumes(h: Handler) -> None:
        h.json({"items": app.ctx().service.resumes()})

    @route("GET", "resumes/:resume_id")
    def resume_get(h: Handler, resume_id: str) -> None:
        h.json(app.ctx().service.resume(resume_id))

    @route("PUT", "resumes/:resume_id")
    def resume_put(h: Handler, resume_id: str) -> None:
        h.json(app.ctx().service.update_resume(resume_id, h.body()))

    @route("POST", "resumes/:resume_id/publish")
    def resume_publish(h: Handler, resume_id: str) -> None:
        app.ctx().service.publish_resume(resume_id)
        h.json({"ok": True})

    @route("GET", "resumes/:resume_id/views")
    def resume_views(h: Handler, resume_id: str) -> None:
        h.json({"items": app.ctx().service.resume_views(resume_id)})

    @route("GET", "suggest/skills")
    def suggest_skills(h: Handler) -> None:
        text = (h.query.get("text") or "").strip()
        h.json({"items": app.ctx().service.skill_suggest(text) if len(text) >= 2 else []})

    # ---- чаты --------------------------------------------------------------

    @route("GET", "chats")
    def chats(h: Handler) -> None:
        h.json(app.ctx().service.chats(h.query.get("from") or None, h.query.get("unread") == "1"))

    @route("GET", "chats/:chat_id")
    def chat_get(h: Handler, chat_id: str) -> None:
        h.json(app.ctx().service.chat_messages(int(chat_id)))

    @route("POST", "chats/:chat_id/send")
    def chat_send(h: Handler, chat_id: str) -> None:
        app.ctx().service.chat_send(int(chat_id), str(h.body().get("text") or ""))
        h.json({"ok": True})

    @route("POST", "chats/:chat_id/leave")
    def chat_leave(h: Handler, chat_id: str) -> None:
        app.ctx().service.chat_leave(int(chat_id))
        h.json({"ok": True})

    @route("POST", "chats/:chat_id/draft")
    def chat_draft(h: Handler, chat_id: str) -> None:
        h.json({"text": app.ctx().service.chat_ai_draft(int(chat_id))})

    # ---- вакансии и чёрные списки -------------------------------------------

    def vac_args(body: dict) -> list[str]:
        args = body.get("args") or []
        if isinstance(args, str):
            args = shlex.split(args)
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ApiError("Некорректные параметры поиска")
        return args

    @route("POST", "vacancies/search")
    def vacancies_search(h: Handler) -> None:
        body = h.body()
        resume_id = str(body.get("resume_id") or "")
        if not resume_id:
            raise ApiError("Выберите резюме")
        h.json(app.ctx().service.search(vac_args(body), resume_id, int(body.get("page") or 0)))

    @route("POST", "vacancies/check")
    def vacancies_check(h: Handler) -> None:
        body = h.body()
        h.json(app.ctx().service.vacancy_check(vac_args(body), str(body.get("resume_id") or ""), str(body.get("vacancy_id") or "")))

    @route("POST", "vacancies/apply")
    def vacancies_apply(h: Handler) -> None:
        body = h.body()
        app.ctx().service.apply(str(body.get("resume_id") or ""), str(body.get("vacancy_id") or ""), str(body.get("letter") or ""))
        h.json({"ok": True})

    @route("GET", "blacklist")
    def blacklist_get(h: Handler) -> None:
        kind = h.query.get("kind") or "vacancies"
        if kind not in ("vacancies", "employers"):
            raise ApiError("Неизвестный список")
        h.json(app.ctx().service.blacklisted(kind, page(h)))

    @route("POST", "blacklist")
    def blacklist_set(h: Handler) -> None:
        body = h.body()
        kind = body.get("kind")
        item_id = str(body.get("id") or "")
        on = bool(body.get("on", True))
        if not item_id:
            raise ApiError("Не указан id")
        if kind == "vacancies":
            app.ctx().service.blacklist_vacancy(item_id, on)
        elif kind == "employers":
            app.ctx().service.blacklist_employer(item_id, on)
        else:
            raise ApiError("Неизвестный список")
        h.json({"ok": True})

    # ---- отклики -----------------------------------------------------------

    @route("GET", "negotiations")
    def negotiations(h: Handler) -> None:
        status = h.query.get("status") or "all"
        h.json(app.ctx().service.negotiations(status, page(h)))

    @route("POST", "negotiations/:nid/hide")
    def negotiation_hide(h: Handler, nid: str) -> None:
        body = h.body()
        app.ctx().service.negotiation_hide(nid, decline=bool(body.get("decline")), delete_chat=bool(body.get("delete_chat")))
        h.json({"ok": True})

    # ---- лог -------------------------------------------------------------

    @route("GET", "log")
    def log_get(h: Handler) -> None:
        level = h.query.get("level") or None
        h.json(app.ctx().service.read_log(level=level))

    @route("DELETE", "log")
    def log_clear(h: Handler) -> None:
        app.ctx().service.clear_log()
        h.json({"ok": True})

    return Handler


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(base_dir: Path, profile: str, host: str, port: int, token: str | None) -> None:
    app = PanelApp(base_dir, profile, token)
    server = PanelServer((host, port), make_handler(app))
    app.scheduler.start()
    print(f"Панель: http://{'localhost' if host in ('0.0.0.0', '::', '127.0.0.1') else host}:{port}", flush=True)
    print(f"Конфиг: {base_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.scheduler.stop()
        app.jobs.shutdown()
        server.server_close()
