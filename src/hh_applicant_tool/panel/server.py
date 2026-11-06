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
import secrets
import shlex
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from ..utils.package import get_package_version
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


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class PanelApp:
    def __init__(self, config_dir: Path, token: str | None = None) -> None:
        self.config_dir = config_dir
        self.token = token or None
        self.secret = secrets.token_bytes(32)
        self.store = PanelStore(config_dir)
        self.jobs = JobManager(self.store, config_dir)
        self.scheduler = Scheduler(self.store, self.jobs)
        self.settings = Settings(config_dir)
        self.stats = Stats(self.store, config_dir)
        self._schema: dict[str, Any] | None = None
        self.started_at = time.time()

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is None:
            self._schema = build_schema()
        return self._schema

    # ---- авторизация -------------------------------------------------

    def session_value(self) -> str:
        assert self.token
        return hmac.new(self.secret, self.token.encode(), hashlib.sha256).hexdigest()

    def check_token(self, value: str) -> bool:
        return bool(self.token) and hmac.compare_digest(value.encode(), self.token.encode())


def make_handler(app: PanelApp) -> type[BaseHTTPRequestHandler]:
    static_root = resources.files(STATIC_PACKAGE) / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "hh-panel"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Не пишем каждый запрос в log.txt утилиты: фронт опрашивает
            # сервер раз в несколько секунд и забил бы лог
            pass

        # ---- утилиты ответа ----------------------------------------------

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

        def _json(self, data: Any, status: int = 200, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _error(self, message: str, status: int = 400) -> None:
            # Тело запроса могло остаться непрочитанным: закрываем соединение,
            # иначе его остаток примут за следующий запрос
            self.close_connection = True
            self._json({"error": message}, status, extra={"Connection": "close"})

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError("Слишком большой запрос", 413)
            raw = self.rfile.read(length) if length else b""
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
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if not path.startswith("/api/"):
                    if method not in ("GET", "HEAD"):
                        raise ApiError("Метод не поддерживается", 405)
                    self._static(path)
                    return
                if method != "GET" and self.headers.get("X-Panel-Request") != "1":
                    raise ApiError("Запрос отклонён", 403)
                if path == "/api/login" and method == "POST":
                    self._login()
                    return
                if path == "/api/session":
                    self._json({"auth_required": bool(app.token), "authorized": self._authorized()})
                    return
                if not self._authorized():
                    raise ApiError("Нужно войти", 401)
                handler = ROUTES.get((method, path))
                if handler is None:
                    # Маршруты с id: /api/jobs/<id>[/action]
                    parts = path.strip("/").split("/")
                    if len(parts) >= 3 and parts[1] in ("jobs", "schedule"):
                        handler = ROUTES.get((method, f"/api/{parts[1]}/:id" + ("/" + parts[3] if len(parts) > 3 else "")))
                        if handler:
                            handler(self, query, parts[2])
                            return
                    raise ApiError("Не найдено", 404)
                handler(self, query)
            except ApiError as ex:
                self._error(str(ex), ex.status)
            except (SettingsError, ScheduleError) as ex:
                self._error(str(ex), 400)
            except RuntimeError as ex:
                self._error(str(ex), 409)
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
                self._json({"ok": True})
                return
            body = self._body()
            if not app.check_token(str(body.get("token") or "")):
                # Немного тормозим перебор
                time.sleep(1)
                raise ApiError("Неверный токен", 401)
            cookie = f"{COOKIE_NAME}={app.session_value()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
            self._json({"ok": True}, extra={"Set-Cookie": cookie})

        # ---- SSE ---------------------------------------------------------

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
            last_status = None
            last_ping = time.monotonic()
            while True:
                text, offset = app.jobs.read_output(job, offset)
                if text:
                    self._event("output", {"text": text, "offset": offset})
                if job.status != last_status:
                    last_status = job.status
                    self._event("status", job.to_dict())
                if job.status in FINAL and not text:
                    # Дочитали всё после завершения
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

    # ---- обработчики API ---------------------------------------------------

    def info(h: Handler, q: dict) -> None:
        h._json(
            {
                "version": get_package_version(),
                "config_dir": str(app.config_dir),
                "auth_required": bool(app.token),
                "started_at": app.started_at,
            }
        )

    def schema(h: Handler, q: dict) -> None:
        h._json(app.schema)

    def profile(h: Handler, q: dict) -> None:
        h._json(app.stats.profile())

    def jobs_list(h: Handler, q: dict) -> None:
        h._json({"jobs": app.jobs.list()})

    def jobs_create(h: Handler, q: dict) -> None:
        body = h._body()
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
        job = app.jobs.submit(args, source="manual", title=str(body.get("title") or operation_title(args)))
        h._json(job.to_dict(), 201)

    def job_get(h: Handler, q: dict, job_id: str) -> None:
        job = app.jobs.get(job_id)
        if not job:
            raise ApiError("Задача не найдена", 404)
        h._json(job.to_dict())

    def job_stream(h: Handler, q: dict, job_id: str) -> None:
        try:
            offset = max(0, int(q.get("offset") or 0))
        except ValueError:
            offset = 0
        h.stream_job(job_id, offset)

    def job_stop(h: Handler, q: dict, job_id: str) -> None:
        if not app.jobs.stop(job_id):
            raise ApiError("Задачу нельзя остановить", 409)
        h._json({"ok": True})

    def job_input(h: Handler, q: dict, job_id: str) -> None:
        text = str(h._body().get("text") or "")
        if not app.jobs.send_input(job_id, text):
            raise ApiError("Задача не ждёт ввода", 409)
        h._json({"ok": True})

    def job_delete(h: Handler, q: dict, job_id: str) -> None:
        if not app.jobs.delete(job_id):
            raise ApiError("Удалить можно только завершённую задачу", 409)
        h._json({"ok": True})

    def schedule_list(h: Handler, q: dict) -> None:
        h._json({"items": app.scheduler.list()})

    def schedule_save(h: Handler, q: dict) -> None:
        items = h._body().get("items")
        if not isinstance(items, list):
            raise ApiError("Ожидается список задач")
        h._json({"items": app.scheduler.save(items)})

    def schedule_run(h: Handler, q: dict, item_id: str) -> None:
        job = app.scheduler.run_now(item_id)
        if not job:
            raise ApiError("Задача расписания не найдена", 404)
        h._json(job.to_dict(), 201)

    def config_get(h: Handler, q: dict) -> None:
        h._json(app.settings.read_raw())

    def config_put(h: Handler, q: dict) -> None:
        app.settings.write_raw(str(h._body().get("text") or ""))
        h._json(app.settings.read_raw())

    def config_validate(h: Handler, q: dict) -> None:
        try:
            app.settings.validate_raw(str(h._body().get("text") or ""))
        except SettingsError as ex:
            h._json({"ok": False, "error": str(ex)})
            return
        h._json({"ok": True})

    def config_restore(h: Handler, q: dict) -> None:
        app.settings.restore_backup()
        h._json(app.settings.read_raw())

    def models_get(h: Handler, q: dict) -> None:
        h._json(app.settings.read_models())

    def models_put(h: Handler, q: dict) -> None:
        sections = h._body().get("sections")
        if not isinstance(sections, dict):
            raise ApiError("Ожидаются секции моделей")
        h._json(app.settings.save_models(sections))

    def _task_body(h: Handler) -> tuple[str, dict]:
        body = h._body()
        key = body.get("key")
        if key not in AI_TASK_KEYS:
            raise ApiError("Неизвестная задача")
        return key, body.get("values") or {}

    def models_test(h: Handler, q: dict) -> None:
        key, values = _task_body(h)
        image = None
        if key == "captcha":
            image = (resources.files(STATIC_PACKAGE) / "static" / "img" / "captcha-sample.png").read_bytes()
        h._json(app.settings.test_model(key, values, image))

    def models_remote(h: Handler, q: dict) -> None:
        key, values = _task_body(h)
        h._json({"models": app.settings.list_remote_models(key, values)})

    def letters_get(h: Handler, q: dict) -> None:
        h._json(app.settings.read_letters())

    def letters_put(h: Handler, q: dict) -> None:
        h._json(app.settings.save_letters(h._body()))

    def letters_preview(h: Handler, q: dict) -> None:
        body = h._body()
        try:
            text = app.settings.preview(str(body.get("text") or ""))
        except SettingsError as ex:
            h._json({"ok": False, "error": str(ex)})
            return
        h._json({"ok": True, "text": text})

    def stats_get(h: Handler, q: dict) -> None:
        h._json({"stats": app.stats.cached()})

    def stats_refresh(h: Handler, q: dict) -> None:
        h._json({"stats": app.stats.refresh()})

    ROUTES: dict[tuple[str, str], Callable[..., None]] = {
        ("GET", "/api/info"): info,
        ("GET", "/api/schema"): schema,
        ("GET", "/api/profile"): profile,
        ("GET", "/api/jobs"): jobs_list,
        ("POST", "/api/jobs"): jobs_create,
        ("GET", "/api/jobs/:id"): job_get,
        ("GET", "/api/jobs/:id/stream"): job_stream,
        ("POST", "/api/jobs/:id/stop"): job_stop,
        ("POST", "/api/jobs/:id/input"): job_input,
        ("DELETE", "/api/jobs/:id"): job_delete,
        ("GET", "/api/schedule"): schedule_list,
        ("PUT", "/api/schedule"): schedule_save,
        ("POST", "/api/schedule/:id/run"): schedule_run,
        ("GET", "/api/config"): config_get,
        ("PUT", "/api/config"): config_put,
        ("POST", "/api/config/validate"): config_validate,
        ("POST", "/api/config/restore"): config_restore,
        ("GET", "/api/models"): models_get,
        ("PUT", "/api/models"): models_put,
        ("POST", "/api/models/test"): models_test,
        ("POST", "/api/models/remote"): models_remote,
        ("GET", "/api/letters"): letters_get,
        ("PUT", "/api/letters"): letters_put,
        ("POST", "/api/letters/preview"): letters_preview,
        ("GET", "/api/stats"): stats_get,
        ("POST", "/api/stats/refresh"): stats_refresh,
    }

    return Handler


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(config_dir: Path, host: str, port: int, token: str | None) -> None:
    app = PanelApp(config_dir, token)
    server = PanelServer((host, port), make_handler(app))
    app.scheduler.start()
    print(f"Панель: http://{'localhost' if host in ('0.0.0.0', '::', '127.0.0.1') else host}:{port}", flush=True)
    print(f"Конфиг: {config_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.scheduler.stop()
        app.jobs.shutdown()
        server.server_close()
