"""HTTP-обёртка над UI.

Команда `ui` открывает нативное окно через pywebview и общается с Python
через JS-мост. В headless-окружении (сервер, Docker-контейнер) окна нет,
поэтому здесь тот же самый интерфейс (`index.html` + существующий `Api`)
отдаётся по HTTP, а браузер открывается уже на стороне пользователя.

Мост `pywebview.api.<method>()` эмулируется через `fetch('/api/call')`,
а серверные пуш-вызовы (`updateProgress`, `onAuthEvent`), которые в родном
UI выполнялись через `window.evaluate_js(...)`, транслируются клиенту через
Server-Sent Events (`/events`).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from . import TEMPLATES_DIR
from .api import Api

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

# Скрипт-шим внедряется сразу после app.js. К этому моменту app.js уже
# зарегистрировал слушатель `pywebviewready`, поэтому мы определяем
# `window.pywebview.api` и вручную бросаем это событие.
_SHIM = """
<script>
(function () {
  function call(method, args) {
    return fetch('api/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method: method, args: Array.prototype.slice.call(args) })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res && res.__error__) throw new Error(res.message || 'API error');
      return res.result;
    });
  }
  window.pywebview = {
    api: new Proxy({}, {
      get: function (_t, prop) {
        if (typeof prop !== 'string') return undefined;
        return function () { return call(prop, arguments); };
      }
    })
  };
  try {
    var es = new EventSource('events');
    es.onmessage = function (e) {
      try { (0, eval)(e.data); } catch (err) { console.error('push eval error', err); }
    };
  } catch (e) { console.error('SSE init failed', e); }
  window.dispatchEvent(new Event('pywebviewready'));
})();
</script>
"""

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class _Broker:
    """Рассылает пуш-сообщения всем подключённым SSE-клиентам."""

    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[str]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, message: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(message)


class _SSEWindow:
    """Заглушка окна pywebview.

    `Api` пушит события в UI единственным методом `evaluate_js(js)`.
    Мы перенаправляем строку JS всем SSE-клиентам, которые её выполняют.
    """

    def __init__(self, broker: _Broker) -> None:
        self._broker = broker

    def evaluate_js(self, js: str) -> None:
        self._broker.publish(js)


def _build_index() -> bytes:
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    marker = '<script src="js/app.js"></script>'
    if marker in html:
        html = html.replace(marker, marker + _SHIM, 1)
    else:  # на случай, если разметку поменяют
        html = html.replace("</body>", _SHIM + "</body>", 1)
    return html.encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    # Проставляются в serve()
    api: Api
    broker: _Broker
    server_version = "hh-applicant-tool-webui"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.debug("%s - %s", self.address_string(), format % args)

    # --- helpers ---
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_static(self, path: str) -> Path | None:
        rel = path.lstrip("/")
        if not rel:
            return None
        candidate = (TEMPLATES_DIR / rel).resolve()
        try:
            candidate.relative_to(TEMPLATES_DIR.resolve())
        except ValueError:  # выход за пределы каталога шаблонов
            return None
        if candidate.is_file():
            return candidate
        return None

    # --- routes ---
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_bytes(_build_index(), _MIME[".html"])
            return
        if path == "/events":
            self._handle_events()
            return
        static = self._resolve_static(path)
        if static is not None:
            content_type = _MIME.get(
                static.suffix, "application/octet-stream"
            )
            self._send_bytes(static.read_bytes(), content_type)
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/call":
            self.send_error(404, "Not Found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            method = payload.get("method")
            args = payload.get("args") or []
        except (ValueError, TypeError):
            self._send_json(
                {"__error__": True, "message": "Bad request"}, status=400
            )
            return

        if (
            not isinstance(method, str)
            or method.startswith("_")
            or not hasattr(self.api, method)
        ):
            self._send_json(
                {"__error__": True, "message": f"Unknown method: {method}"},
                status=404,
            )
            return

        func = getattr(self.api, method)
        if not callable(func):
            self._send_json(
                {"__error__": True, "message": f"Not callable: {method}"},
                status=400,
            )
            return

        try:
            result = func(*args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("web-ui call %s failed", method)
            self._send_json(
                {"__error__": True, "message": str(exc)}, status=500
            )
            return
        self._send_json({"result": result})

    def _handle_events(self) -> None:
        q = self.broker.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                # Значение может содержать переносы строк -> несколько data:
                for line in message.split("\n"):
                    self.wfile.write(("data: " + line + "\n").encode("utf-8"))
                self.wfile.write(b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.broker.unsubscribe(q)


def serve(tool: HHApplicantTool, *, host: str = "127.0.0.1", port: int = 8080) -> None:
    broker = _Broker()
    api = Api(tool)
    api.set_window(_SSEWindow(broker))

    handler = type("BoundHandler", (_Handler,), {"api": api, "broker": broker})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    logger.info("Веб-интерфейс запущен: http://%s:%d", shown, port)
    if host == "0.0.0.0":
        logger.info(
            "Слушаю на всех интерфейсах. Открывайте по адресу хоста "
            "(в Docker — проброшенный порт)."
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка веб-интерфейса...")
    finally:
        httpd.shutdown()
        httpd.server_close()
