"""HTTP-сервер веб-панели.

Идея: панель не хардкодит команды. Она интроспектит argparse-парсер утилиты и
автоматически строит формы для КАЖДОЙ операции и КАЖДОГО её флага. Запуск —
через подпроцесс `python -m hh_applicant_tool ...`, то есть буквально тот же CLI,
с живым выводом (stdout) и вводом (stdin, для интерактивных команд вроде auth).
Плюс полный редактор config.json.

Только для localhost: панель умеет запускать произвольные команды утилиты и
редактировать токены — наружу её выставлять нельзя.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import time as _time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..constants import CONFIG_FILENAME

if TYPE_CHECKING:
    from ..main import HHApplicantTool


# --------------------------------------------------------------------------- #
# Интроспекция парсера                                                          #
# --------------------------------------------------------------------------- #

def _short_flag(flags: list[str]) -> str | None:
    for f in flags:
        if f.startswith("-") and not f.startswith("--"):
            return f
    return flags[0] if flags else None


def _long_flag(flags: list[str]) -> str | None:
    for f in flags:
        if f.startswith("--"):
            return f
    return flags[0] if flags else None


def _action_spec(action: argparse.Action) -> dict[str, Any]:
    flags = list(action.option_strings)
    cls = action.__class__.__name__
    choices = list(action.choices) if action.choices else None

    if cls in ("_StoreTrueAction", "_StoreFalseAction"):
        kind = "flag"
    elif cls == "BooleanOptionalAction":
        kind = "bool"
    elif cls == "_CountAction":
        kind = "count"
    elif choices:
        kind = "choice"
    elif action.type is int:
        kind = "int"
    elif action.type is float:
        kind = "float"
    else:
        kind = "text"

    multi = action.nargs in ("*", "+") or (
        isinstance(action.nargs, int) and action.nargs > 1
    )

    default = action.default
    try:
        json.dumps(default)
    except TypeError:
        default = None
    if default is argparse.SUPPRESS:
        default = None

    return {
        "dest": action.dest,
        "flags": flags,
        "flag": _long_flag(flags),
        "short": _short_flag(flags),
        "help": action.help or "",
        "kind": kind,
        "choices": choices,
        "multi": bool(multi),
        "required": bool(getattr(action, "required", False)),
        "positional": not flags,
        "default": default if kind != "flag" else None,
    }


def _parser_args(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    out = []
    for a in parser._actions:
        if a.__class__.__name__ == "_HelpAction":
            continue
        if isinstance(a, argparse._SubParsersAction):
            continue
        out.append(_action_spec(a))
    return out


def build_schema(parser: argparse.ArgumentParser) -> dict[str, Any]:
    globals_ = _parser_args(parser)

    sub = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    operations = []
    if sub is not None:
        canonical_help = {
            ca.dest: (ca.help or "") for ca in sub._choices_actions
        }
        groups: dict[int, dict[str, Any]] = {}
        for name, subp in sub.choices.items():
            g = groups.setdefault(id(subp), {"parser": subp, "names": []})
            g["names"].append(name)
        for g in groups.values():
            names = g["names"]
            canonical = next((n for n in names if n in canonical_help), names[0])
            aliases = [n for n in names if n != canonical]
            operations.append(
                {
                    "name": canonical,
                    "aliases": aliases,
                    "help": canonical_help.get(canonical, "")
                    or (g["parser"].description or ""),
                    "args": _parser_args(g["parser"]),
                }
            )
        operations.sort(key=lambda o: o["name"])

    return {"globals": globals_, "operations": operations}


# --------------------------------------------------------------------------- #
# Запуск команд (подпроцесс) + SSE                                             #
# --------------------------------------------------------------------------- #

class Runner:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._subs: set[queue.Queue] = set()
        self._sub_lock = threading.Lock()
        self._buffer: list[dict] = []  # последние события для реконнекта

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._sub_lock:
            self._subs.add(q)
            backlog = list(self._buffer)
        for ev in backlog:
            q.put(ev)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            self._subs.discard(q)

    def _emit(self, ev: dict) -> None:
        with self._sub_lock:
            if ev.get("type") == "start":
                self._buffer = []
            self._buffer.append(ev)
            self._buffer = self._buffer[-1000:]
            subs = list(self._subs)
        for q in subs:
            q.put(ev)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv: list[str]) -> tuple[bool, str]:
        with self._lock:
            if self.is_running():
                return False, "Команда уже выполняется"
            cmd = [sys.executable, "-u", "-m", "hh_applicant_tool", *argv]
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            # Сигнал утилите: капчу отдавать base64-маркером, панель отрисует
            env["HH_WEBPANEL"] = "1"
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
            except Exception as e:  # noqa: BLE001
                return False, str(e)
            threading.Thread(target=self._pump, daemon=True).start()
            self._emit(
                {"type": "start", "cmd": "hh-applicant-tool " + " ".join(argv)}
            )
            return True, "started"

    def _pump(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in proc.stdout:
                self._emit({"type": "out", "line": line.rstrip("\n")})
        except Exception:  # noqa: BLE001
            pass
        code = proc.wait()
        self._emit({"type": "exit", "code": code})

    def send_stdin(self, line: str) -> bool:
        if self.is_running() and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
                self._emit({"type": "in", "line": line})
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

    def stop(self) -> bool:
        if self.is_running() and self.proc:
            self.proc.terminate()
            return True
        return False


class Scheduler:
    """Простой планировщик: раз в сутки в заданное время запускает команду.

    Работает, пока жив процесс панели (контейнер с restart: unless-stopped).
    Время — локальное для контейнера (проброшен /etc/localtime хоста).
    """

    def __init__(self, runner: Runner, path: Path) -> None:
        self._runner = runner
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    def _default(self) -> dict:
        return {
            "enabled": False,
            "time": "09:00",
            "argv": ["apply-vacancies", "--ai"],
            "last_run_date": None,
        }

    def _load(self) -> dict:
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            base = self._default()
            base.update({k: d[k] for k in base if k in d})
            return base
        except Exception:  # noqa: BLE001
            return self._default()

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def save(self, enabled: bool, time_str: str, argv: list[str]) -> dict:
        with self._lock:
            self._data["enabled"] = bool(enabled)
            self._data["time"] = time_str
            self._data["argv"] = [str(x) for x in argv]
            self._persist()
            return dict(self._data)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(25)

    def _tick(self) -> None:
        with self._lock:
            d = dict(self._data)
        if not d.get("enabled") or not d.get("argv"):
            return
        now = datetime.datetime.now()
        if now.strftime("%H:%M") != d.get("time"):
            return
        today = now.strftime("%Y-%m-%d")
        if d.get("last_run_date") == today:
            return
        if self._runner.is_running():
            return  # занят — попробуем в следующий тик (в пределах той же минуты)
        ok, _msg = self._runner.start(list(d["argv"]))
        if ok:
            with self._lock:
                self._data["last_run_date"] = today
                self._persist()


# --------------------------------------------------------------------------- #
# HTTP-обработчик                                                              #
# --------------------------------------------------------------------------- #

class _Handler(BaseHTTPRequestHandler):
    schema: dict
    runner: Runner
    config_file: Path
    scheduler: Any = None
    tool: Any = None
    server_version = "hh-webpanel"

    AI_SECTIONS = [
        ("openai_cover_letter", "Сопроводительные письма"),
        ("openai_vacancy_filter", "Фильтр вакансий"),
        ("openai_captcha", "Капча (нужна vision-модель)"),
    ]

    def log_message(self, *a):  # тише
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    # ---- routes ---- #
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._html(PAGE)
        elif path == "/api/schema":
            self._json(self.schema)
        elif path == "/api/config":
            self._json({"text": self._read_config()})
        elif path == "/api/ai-config":
            self._json(self._ai_sections())
        elif path == "/api/schedule":
            self._json(self.scheduler.get() if self.scheduler else {})
        elif path == "/api/templates":
            self._json(
                {"templates": self._load_config_dict().get("letter_templates") or []}
            )
        elif path == "/api/state":
            self._json({"running": self.runner.is_running()})
        elif path == "/api/stream":
            self._stream()
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/run":
            argv = self._body().get("argv") or []
            if not isinstance(argv, list) or not all(
                isinstance(x, str) for x in argv
            ):
                self._json({"ok": False, "error": "Неверный argv"}, 400)
                return
            ok, msg = self.runner.start(argv)
            self._json({"ok": ok, "message": msg}, 200 if ok else 409)
        elif path == "/api/stdin":
            line = self._body().get("line", "")
            self._json({"ok": self.runner.send_stdin(str(line))})
        elif path == "/api/stop":
            self._json({"ok": self.runner.stop()})
        elif path == "/api/config":
            text = self._body().get("text", "")
            try:
                json.loads(text)  # валидация
            except ValueError as e:
                self._json({"ok": False, "error": f"Некорректный JSON: {e}"}, 400)
                return
            try:
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
                self.config_file.write_text(text, encoding="utf-8")
                self._json({"ok": True})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/ai-config":
            self._save_ai_sections(self._body().get("sections") or {})
        elif path == "/api/ai-test":
            self._ai_test(self._body())
        elif path == "/api/schedule":
            b = self._body()
            time_str = str(b.get("time") or "").strip()
            argv = b.get("argv") or []
            import re as _re

            if not _re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_str):
                self._json({"ok": False, "error": "Время в формате ЧЧ:ММ"}, 400)
                return
            if not isinstance(argv, list) or not all(
                isinstance(x, str) for x in argv
            ):
                self._json({"ok": False, "error": "Неверная команда"}, 400)
                return
            data = self.scheduler.save(bool(b.get("enabled")), time_str, argv)
            self._json({"ok": True, "schedule": data})
        elif path == "/api/templates":
            tpls = self._body().get("templates")
            if not isinstance(tpls, list):
                self._json({"ok": False, "error": "Ожидался список"}, 400)
                return
            clean = []
            for t in tpls:
                if not isinstance(t, dict):
                    continue
                clean.append(
                    {
                        "name": str(t.get("name") or ""),
                        "keywords": str(t.get("keywords") or ""),
                        "text": str(t.get("text") or ""),
                    }
                )
            cfg = self._load_config_dict()
            if clean:
                cfg["letter_templates"] = clean
            else:
                cfg.pop("letter_templates", None)
            try:
                self.config_file.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                self._json({"ok": True})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_error(404)

    # ---- helpers ---- #
    def _html(self, s: str):
        body = s.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_config(self) -> str:
        if self.config_file.exists():
            return self.config_file.read_text(encoding="utf-8", errors="replace")
        return "{}"

    def _load_config_dict(self) -> dict:
        try:
            data = json.loads(self._read_config())
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _ai_sections(self) -> dict:
        cfg = self._load_config_dict()
        return {
            key: {
                "label": label,
                "values": cfg.get(key) or {},
            }
            for key, label in self.AI_SECTIONS
        }

    def _save_ai_sections(self, sections: dict) -> None:
        valid = {k for k, _ in self.AI_SECTIONS}
        cfg = self._load_config_dict()
        for key, values in sections.items():
            if key not in valid or not isinstance(values, dict):
                continue
            existing = cfg.get(key) or {}
            merged = dict(values)
            # пустой api_key = не менять текущий
            if not merged.get("api_key"):
                if existing.get("api_key"):
                    merged["api_key"] = existing["api_key"]
                else:
                    merged.pop("api_key", None)
            merged = {
                k: v for k, v in merged.items() if v not in (None, "")
            }
            if merged:
                cfg[key] = merged
            else:
                cfg.pop(key, None)
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._json({"ok": True})
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)}, 500)

    def _ai_test(self, b: dict) -> None:
        import time

        import requests

        from .. import ai as ai_mod

        base_url = (b.get("base_url") or "").strip()
        if not base_url:
            self._json({"ok": False, "error": "Не задан base_url"}, 400)
            return

        def num(v, default, cast):
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        try:
            client = ai_mod.ChatOpenAI(
                api_key=(b.get("api_key") or "x"),
                base_url=base_url,
                model=(b.get("model") or None),
                temperature=num(b.get("temperature"), 0.0, float),
                max_completion_tokens=num(
                    b.get("max_completion_tokens"), 512, int
                ),
                system_prompt=(b.get("system") or None),
                timeout=num(b.get("timeout"), 120.0, float),
                session=(
                    self.tool.openai_session
                    if self.tool
                    else requests.Session()
                ),
            )
            t0 = time.monotonic()
            resp = client.complete(b.get("message") or "Привет!")
            elapsed = int((time.monotonic() - t0) * 1000)
            self._json({"ok": True, "response": resp, "elapsed_ms": elapsed})
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)})

    def _stream(self):
        q = self.runner.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.runner.unsubscribe(q)


def serve(tool: HHApplicantTool, *, host: str = "127.0.0.1", port: int = 8090) -> None:
    import logging

    logger = logging.getLogger(__package__)

    schema = build_schema(tool._parser)
    runner = Runner()
    config_file = Path(tool.config._config_path)
    scheduler = Scheduler(runner, config_file.parent / "webpanel_schedule.json")
    scheduler.start()

    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "schema": schema,
            "runner": runner,
            "config_file": config_file,
            "scheduler": scheduler,
            "tool": tool,
        },
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    logger.warning("Веб-панель: http://%s:%d  (только для localhost!)", shown, port)
    print(f"Веб-панель запущена: http://{shown}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------- #
# Встроенный фронтенд                                                          #
# --------------------------------------------------------------------------- #

PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HH Tool — Панель</title>
<style>
  :root{--bg:#0f172a;--panel:#1e293b;--panel2:#111827;--line:#334155;--txt:#e2e8f0;--muted:#94a3b8;--accent:#3b82f6;--danger:#ef4444;--ok:#22c55e;}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt);height:100vh;display:flex}
  a{color:var(--accent)}
  #side{width:250px;flex-shrink:0;background:var(--panel2);border-right:1px solid var(--line);overflow-y:auto;padding:10px}
  #side h1{font-size:14px;margin:6px 8px 12px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
  .navitem{display:block;padding:7px 10px;border-radius:7px;cursor:pointer;font-size:13px;color:var(--txt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .navitem:hover{background:#0b1220}
  .navitem.active{background:var(--accent);color:#fff}
  .navsep{margin:10px 8px 4px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  #content{flex:1;overflow-y:auto;padding:18px 20px}
  h2{margin:0 0 4px;font-size:18px}
  .hint{color:var(--muted);font-size:12.5px;margin:0 0 14px}
  .field{margin-bottom:12px}
  .field label{display:block;font-size:12.5px;margin-bottom:4px}
  .field .fh{color:var(--muted);font-size:11.5px;margin-top:3px;white-space:pre-wrap}
  input[type=text],input[type=number],select,textarea{width:100%;background:var(--panel);border:1px solid var(--line);color:var(--txt);border-radius:7px;padding:7px 9px;font-size:13px;font-family:inherit}
  textarea{resize:vertical}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
  .chk{display:flex;align-items:center;gap:8px;font-size:13px}
  .chk input{width:16px;height:16px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:6px}
  button{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:8px 14px;font-size:13px;cursor:pointer}
  button.sec{background:var(--panel);border:1px solid var(--line);color:var(--txt)}
  button.danger{background:var(--danger)}
  button:disabled{opacity:.5;cursor:not-allowed}
  details{border:1px solid var(--line);border-radius:8px;margin-bottom:14px;background:var(--panel2)}
  details>summary{cursor:pointer;padding:8px 12px;font-size:13px;color:var(--muted)}
  details>.inner{padding:12px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}
  @media(max-width:720px){.grid{grid-template-columns:1fr}}
  .badge{display:inline-block;font-size:10.5px;background:var(--line);color:var(--muted);border-radius:5px;padding:1px 6px;margin-left:6px}
  #console{height:34vh;flex-shrink:0;min-height:80px;background:#0a0f1a;border-top:1px solid var(--line);display:flex;flex-direction:column}
  #conresize{height:7px;flex-shrink:0;cursor:ns-resize;background:var(--line);position:relative}
  #conresize:hover,#conresize.drag{background:var(--accent)}
  #conresize::after{content:"";position:absolute;left:50%;top:2px;transform:translateX(-50%);width:34px;height:3px;border-radius:2px;background:#64748b}
  #contop{display:flex;align-items:center;gap:10px;padding:6px 12px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
  #dot{width:9px;height:9px;border-radius:50%;background:#475569}
  #dot.run{background:var(--ok);box-shadow:0 0 6px var(--ok)}
  #log{flex:1;overflow-y:auto;padding:8px 12px;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
  #log .cmd{color:#7dd3fc}#log .in{color:#fbbf24}#log .exit{color:var(--muted)}#log .err{color:#fca5a5}
  #stdinrow{display:flex;gap:8px;padding:8px 12px;border-top:1px solid var(--line)}
  #stdin{flex:1}
  .warn{background:#78350f;color:#fde68a;font-size:12px;padding:6px 12px}
  code{background:var(--panel);padding:1px 5px;border-radius:4px;font-size:12px}
</style>
</head>
<body>
<div id="side">
  <h1>HH Tool</h1>
  <div id="nav"></div>
</div>
<div id="main">
  <div id="content"></div>
  <div id="console">
    <div id="conresize" title="Потяните, чтобы изменить высоту терминала"></div>
    <div id="contop"><span id="dot"></span><span id="status">простаивает</span><span id="curcmd"></span>
      <span style="margin-left:auto"></span>
      <button id="btn-stop" class="danger" onclick="stopCmd()" disabled>Стоп</button>
      <button class="sec" onclick="clearLog()">Очистить</button>
    </div>
    <div id="log"></div>
    <div id="stdinrow">
      <input id="stdin" type="text" placeholder="ввод для интерактивной команды (Enter) — напр. код из SMS/почты">
      <button class="sec" onclick="sendStdin()">Отправить</button>
    </div>
  </div>
</div>
<script>
let SCHEMA=null, CURRENT=null;
const $=(s)=>document.querySelector(s);

async function boot(){
  SCHEMA=await (await fetch('api/schema')).json();
  renderNav();
  openConfig();
  initStream();
  refreshState();
}

function renderNav(){
  const nav=$('#nav');
  nav.innerHTML='';
  const mk=(label,fn,extra)=>{const d=document.createElement('div');d.className='navitem';d.textContent=label;if(extra)d.innerHTML=label+extra;d.onclick=fn;return d;};
  const sep=(t)=>{const d=document.createElement('div');d.className='navsep';d.textContent=t;return d;};
  nav.appendChild(sep('Настройки'));
  nav.appendChild(mk('⚙ Конфиг (config.json)',()=>openConfig()));
  nav.appendChild(mk('🤖 AI-провайдеры',()=>openAiConfig()));
  nav.appendChild(mk('📝 Шаблоны писем',()=>openTemplates()));
  nav.appendChild(mk('⏰ Расписание',()=>openSchedule()));
  nav.appendChild(mk('⌨ Произвольная команда',()=>openRaw()));
  nav.appendChild(sep('Команды CLI'));
  SCHEMA.operations.forEach(op=>{
    nav.appendChild(mk(op.name, ()=>openOp(op.name), op.aliases&&op.aliases.length?` <span class="badge">${op.aliases.join(', ')}</span>`:''));
  });
}
function setActive(idx){document.querySelectorAll('.navitem').forEach((n,i)=>n.classList.toggle('active',i===idx));}

function fieldFor(a,scope){
  const id=scope+'__'+a.dest;
  const wrap=document.createElement('div');wrap.className='field';
  const help=(a.help||'')+(a.flags&&a.flags.length?('  ['+a.flags.join(' ')+']'):'')+(a.positional?'  (позиционный)':'');
  if(a.kind==='flag'||a.kind==='bool'){
    wrap.innerHTML=`<label class="chk"><input type="checkbox" id="${id}" data-dest="${a.dest}" data-kind="${a.kind}"> <span>${a.dest}</span></label><div class="fh">${esc(help)}</div>`;
    if(a.default===true)setTimeout(()=>{const el=$('#'+CSS.escape(id));if(el)el.checked=true;});
    return wrap;
  }
  let inner='';
  const dv=a.default!=null?String(a.default):'';
  if(a.kind==='choice'){
    inner=`<select id="${id}" data-dest="${a.dest}" data-kind="choice" data-flag="${a.flag||''}" data-pos="${a.positional?1:0}"><option value="">— не задано —</option>`+
      a.choices.map(c=>`<option ${String(c)===dv?'selected':''}>${esc(String(c))}</option>`).join('')+`</select>`;
  } else if(a.kind==='count'){
    inner=`<input type="number" min="0" id="${id}" data-dest="${a.dest}" data-kind="count" data-short="${a.short||a.flag||''}" placeholder="0">`;
  } else {
    const t=(a.kind==='int'||a.kind==='float')?'number':'text';
    const step=a.kind==='float'?' step="any"':'';
    inner=`<input type="${t}"${step} id="${id}" data-dest="${a.dest}" data-kind="${a.kind}" data-flag="${a.flag||''}" data-pos="${a.positional?1:0}" data-multi="${a.multi?1:0}" placeholder="${a.multi?'через пробел':(dv||'')}" value="${a.multi?'':esc(dv)}">`;
  }
  wrap.innerHTML=`<label>${a.dest}${a.required?' <span class="badge">обязательный</span>':''}</label>${inner}<div class="fh">${esc(help)}</div>`;
  return wrap;
}

function openOp(name){
  const op=SCHEMA.operations.find(o=>o.name===name);CURRENT={type:'op',op};
  const c=$('#content');c.innerHTML='';
  const h=document.createElement('div');
  h.innerHTML=`<h2>${op.name}${op.aliases.length?` <span class="badge">${op.aliases.join(', ')}</span>`:''}</h2><p class="hint">${esc(op.help||'')}</p>`;
  c.appendChild(h);
  const form=document.createElement('div');form.id='opform';
  if(op.args.length){const g=document.createElement('div');g.className='grid';op.args.forEach(a=>g.appendChild(fieldFor(a,'op')));form.appendChild(g);}
  else form.innerHTML='<p class="hint">У команды нет параметров.</p>';
  c.appendChild(form);
  const gl=document.createElement('details');
  gl.innerHTML='<summary>Глобальные параметры (профиль, прокси, задержка, verbosity)</summary>';
  const gin=document.createElement('div');gin.className='inner grid';SCHEMA.globals.forEach(a=>gin.appendChild(fieldFor(a,'gl')));gl.appendChild(gin);c.appendChild(gl);
  const row=document.createElement('div');row.className='row';
  row.innerHTML=`<button onclick="runOp()">▶ Запустить</button><button class="sec" onclick="previewOp()">Показать команду</button><span id="preview" class="fh"></span>`;
  c.appendChild(row);
  const idx=Array.from(document.querySelectorAll('.navitem')).findIndex(n=>n.textContent.trim().startsWith(op.name));
  setActive(idx);
}

function collect(scope){
  const out=[];const pos=[];
  document.querySelectorAll(`#content [id^="${scope}__"]`).forEach(el=>{
    const kind=el.dataset.kind;
    if(kind==='flag'||kind==='bool'){if(el.checked)out.push(flagOf(el));return;}
    if(kind==='count'){const n=parseInt(el.value||'0');const f=el.dataset.short;for(let i=0;i<n;i++)out.push(f);return;}
    const v=(el.value||'').trim();if(v==='')return;
    if(el.dataset.pos==='1'){if(el.dataset.multi==='1')v.split(/\s+/).forEach(x=>pos.push(x));else pos.push(v);return;}
    const flag=el.dataset.flag;
    if(el.dataset.multi==='1'){out.push(flag);v.split(/\s+/).forEach(x=>out.push(x));}
    else out.push(flag,v);
  });
  return {options:out,positionals:pos};
}
function flagOf(el){const op=SCHEMA.operations.find(o=>o===CURRENT.op);let a=null;if(CURRENT.op)a=CURRENT.op.args.find(x=>x.dest===el.dataset.dest);if(!a)a=SCHEMA.globals.find(x=>x.dest===el.dataset.dest);return a?a.flag:('--'+el.dataset.dest.replace(/_/g,'-'));}

function buildArgv(){
  const g=collect('gl');const o=collect('op');
  return [...g.options, CURRENT.op.name, ...o.positionals, ...o.options];
}
function previewOp(){$('#preview').textContent='hh-applicant-tool '+buildArgv().join(' ');}
async function runOp(){await run(buildArgv());}

function openRaw(){
  CURRENT={type:'raw'};const c=$('#content');
  c.innerHTML=`<h2>Произвольная команда</h2><p class="hint">Введите аргументы как в CLI (без <code>hh-applicant-tool</code>). Пример: <code>-vv apply-vacancies --search "React" --dry-run</code></p>
  <div class="field"><input id="rawcmd" type="text" placeholder='apply-vacancies --search "Python" --dry-run'></div>
  <div class="row"><button onclick="runRaw()">▶ Запустить</button></div>`;
  setActive(-1);$('#rawcmd').focus();
  $('#rawcmd').addEventListener('keydown',e=>{if(e.key==='Enter')runRaw();});
}
function splitArgs(s){const re=/"([^"]*)"|'([^']*)'|(\S+)/g;const out=[];let m;while((m=re.exec(s)))out.push(m[1]??m[2]??m[3]);return out;}
async function runRaw(){const s=$('#rawcmd').value.trim();if(!s)return;await run(splitArgs(s));}

async function openConfig(){
  CURRENT={type:'config'};const c=$('#content');
  const data=await (await fetch('api/config')).json();
  c.innerHTML=`<h2>Конфиг (config.json)</h2><p class="hint">Полный JSON конфигурации — любые ключи. Здесь же токены (⚠ не показывайте экран посторонним).</p>
  <textarea id="cfg" rows="22" spellcheck="false"></textarea>
  <div class="row"><button onclick="saveConfig()">💾 Сохранить</button><button class="sec" onclick="openConfig()">Перечитать</button><span id="cfgmsg" class="fh"></span></div>`;
  $('#cfg').value=data.text;setActive(0);
}
async function saveConfig(){
  const text=$('#cfg').value;
  const r=await (await fetch('api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})})).json();
  $('#cfgmsg').textContent=r.ok?'✅ сохранено':('❌ '+(r.error||'ошибка'));$('#cfgmsg').style.color=r.ok?'#22c55e':'#fca5a5';
}

// ---- выполнение / консоль ---- #
async function run(argv){
  clearLog();
  const r=await (await fetch('api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({argv})})).json();
  if(!r.ok){addLine('err','⛔ '+(r.message||'не удалось запустить'));}
}
async function stopCmd(){await fetch('api/stop',{method:'POST'});}
async function sendStdin(){const el=$('#stdin');const line=el.value;el.value='';await fetch('api/stdin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line})});}
$('#stdin')&&document.addEventListener('keydown',e=>{if(e.target&&e.target.id==='stdin'&&e.key==='Enter')sendStdin();});

function setRunning(on,cmd){$('#dot').classList.toggle('run',on);$('#status').textContent=on?'выполняется':'простаивает';$('#btn-stop').disabled=!on;if(cmd!==undefined)$('#curcmd').textContent=cmd||'';}
function clearLog(){$('#log').innerHTML='';}
function addLine(cls,text){const l=$('#log');const d=document.createElement('div');if(cls)d.className=cls;d.textContent=text;l.appendChild(d);while(l.children.length>4000)l.removeChild(l.firstChild);l.scrollTop=l.scrollHeight;}
function showCaptcha(b64){
  const l=$('#log');const wrap=document.createElement('div');
  const cap=document.createElement('div');cap.className='err';cap.textContent='🔐 Капча — введите текст в поле ввода снизу и нажмите Enter:';
  const img=document.createElement('img');img.src='data:image/png;base64,'+b64.trim();img.alt='captcha';
  img.style.cssText='max-height:90px;background:#fff;padding:6px;border-radius:6px;margin:6px 0;display:block';
  wrap.appendChild(cap);wrap.appendChild(img);l.appendChild(wrap);l.scrollTop=l.scrollHeight;
  const s=$('#stdin');if(s){s.placeholder='введите текст с капчи и Enter';s.focus();}
}

function initStream(){
  const es=new EventSource('api/stream');
  es.onmessage=(e)=>{let ev;try{ev=JSON.parse(e.data);}catch(_){return;}
    if(ev.type==='start'){setRunning(true,ev.cmd);addLine('cmd','$ '+ev.cmd);}
    else if(ev.type==='out'){
      const MARK='[[WEBCAPTCHA]]';const i=ev.line.indexOf(MARK);
      if(i>=0)showCaptcha(ev.line.slice(i+MARK.length));
      else addLine('',ev.line);
    }
    else if(ev.type==='in'){addLine('in','> '+ev.line);}
    else if(ev.type==='exit'){setRunning(false,'');addLine('exit','— завершено, код '+ev.code+' —');}
  };
  es.onerror=()=>{/* авто-реконнект */};
}
async function refreshState(){try{const s=await (await fetch('api/state')).json();setRunning(!!s.running);}catch(_){}}

// ---- AI-провайдеры ---- #
let AICFG={};
async function openAiConfig(){
  CURRENT={type:'ai'};
  AICFG=await (await fetch('api/ai-config')).json();
  const c=$('#content');
  c.innerHTML=`<h2>AI-провайдеры</h2><p class="hint">OpenAI-совместимые модели для каждой задачи (утилита читает секции <code>openai_*</code>). Пробный запрос шлётся на указанный эндпоинт с ТЕКУЩИМИ значениями формы — можно проверить до сохранения. base_url — полный, с <code>/chat/completions</code>.</p><div id="aisecs"></div>
  <div class="row"><button onclick="saveAiConfig()">💾 Сохранить все секции</button><button class="sec" onclick="openAiConfig()">Перечитать</button><span id="aimsg" class="fh"></span></div>`;
  const box=$('#aisecs');
  Object.keys(AICFG).forEach(key=>box.insertAdjacentHTML('beforeend',aiSectionHtml(key,AICFG[key])));
  Object.keys(AICFG).forEach(key=>{const v=AICFG[key].values||{};
    setVal(key,'base_url',v.base_url);setVal(key,'model',v.model);
    setVal(key,'temperature',v.temperature);setVal(key,'max_completion_tokens',v.max_completion_tokens);setVal(key,'rate_limit',v.rate_limit);
    const ak=document.getElementById('ai__'+key+'__api_key');if(ak&&v.api_key)ak.placeholder='● установлен — пусто = не менять';
  });
  setActive(-1);
}
function setVal(key,f,v){const el=document.getElementById('ai__'+key+'__'+f);if(el&&v!=null&&v!=='')el.value=v;}
function aiSectionHtml(key,sec){
  const p='ai__'+key+'__';
  return `<details open><summary><b>${key}</b> — ${esc(sec.label||'')}</summary><div class="inner">
    <div class="grid">
      <div class="field"><label>API ключ</label><input type="password" id="${p}api_key" autocomplete="new-password" placeholder="ключ провайдера (для LM Studio/Ollama — любое)"></div>
      <div class="field"><label>base_url (полный, с /chat/completions)</label><input type="text" id="${p}base_url" placeholder="https://api.groq.com/openai/v1/chat/completions"></div>
      <div class="field"><label>Модель</label><input type="text" id="${p}model" placeholder="llama-3.3-70b-versatile"></div>
      <div class="field"><label>temperature</label><input type="number" step="any" id="${p}temperature" placeholder="0.4"></div>
      <div class="field"><label>max_completion_tokens</label><input type="number" id="${p}max_completion_tokens" placeholder="800"></div>
      <div class="field"><label>rate_limit (запросов/мин, 0=выкл)</label><input type="number" id="${p}rate_limit" placeholder="0"></div>
    </div>
    <div class="field"><label>Пробный запрос — system (необязательно)</label><textarea id="${p}system" rows="2" placeholder="Ты — ассистент. Отвечай кратко."></textarea></div>
    <div class="field"><label>Пробный запрос — сообщение</label><textarea id="${p}message" rows="2">Ответь одним словом: работает?</textarea></div>
    <div class="row"><button class="sec" onclick="aiTest('${key}')">🧪 Отправить пробный</button><span id="${p}result" class="fh"></span></div>
  </div></details>`;
}
function collectAi(key){
  const g=(f)=>document.getElementById('ai__'+key+'__'+f);
  const out={};
  const ak=g('api_key').value.trim();if(ak)out.api_key=ak;
  ['base_url','model'].forEach(f=>{const v=g(f).value.trim();if(v)out[f]=v;});
  const t=g('temperature').value.trim();if(t!=='')out.temperature=Number(t);
  const mt=g('max_completion_tokens').value.trim();if(mt!=='')out.max_completion_tokens=parseInt(mt);
  const rl=g('rate_limit').value.trim();if(rl!=='')out.rate_limit=parseInt(rl);
  return out;
}
async function saveAiConfig(){
  const sections={};Object.keys(AICFG).forEach(k=>sections[k]=collectAi(k));
  const r=await (await fetch('api/ai-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections})})).json();
  const m=$('#aimsg');m.textContent=r.ok?'✅ сохранено':('❌ '+(r.error||'ошибка'));m.style.color=r.ok?'#22c55e':'#fca5a5';
  if(r.ok)openAiConfig();
}
async function aiTest(key){
  const g=(f)=>document.getElementById('ai__'+key+'__'+f);
  const saved=(AICFG[key]&&AICFG[key].values)||{};
  const body={
    api_key:g('api_key').value.trim()||saved.api_key||'',
    base_url:g('base_url').value.trim()||saved.base_url||'',
    model:g('model').value.trim()||saved.model||'',
    temperature:g('temperature').value.trim()||saved.temperature,
    max_completion_tokens:g('max_completion_tokens').value.trim()||saved.max_completion_tokens,
    system:g('system').value,message:g('message').value,
  };
  const res=g('result');res.style.color='';res.textContent='⏳ запрос...';
  try{
    const r=await (await fetch('api/ai-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(r.ok){res.style.color='#22c55e';res.textContent=`✅ ${r.elapsed_ms} мс — ${r.response}`;}
    else{res.style.color='#fca5a5';res.textContent='❌ '+(r.error||'ошибка');}
  }catch(e){res.style.color='#fca5a5';res.textContent='❌ '+e;}
}

// ---- Шаблоны писем (без AI) ---- #
let TPLS=[];
async function openTemplates(){
  CURRENT={type:'templates'};
  const d=await (await fetch('api/templates')).json();
  TPLS=(d.templates||[]).map(t=>({name:t.name||'',keywords:Array.isArray(t.keywords)?t.keywords.join(', '):(t.keywords||''),text:t.text||''}));
  renderTemplates();
}
function renderTemplates(){
  let h=`<h2>Шаблоны писем (без AI)</h2><p class="hint">Используются, когда AI для писем <b>выключен</b> (запуск без <code>--ai</code>). Письмо выбирается по первому совпадению ключевого слова с названием/описанием вакансии. Шаблон с <b>пустыми</b> ключевыми словами — резервный (Default). Порядок важен: проверка сверху вниз.</p><div id="tpls">`;
  TPLS.forEach((t,i)=>h+=tplCard(t,i));
  h+=`</div><div class="row"><button class="sec" onclick="tplAdd()">＋ Добавить шаблон</button><button onclick="saveTemplates()">💾 Сохранить</button><span id="tpl-msg" class="fh"></span></div>`;
  $('#content').innerHTML=h; setActive(-1);
}
function tplCard(t,i){
  return `<details open><summary><b>${esc(t.name||'(без названия)')}</b></summary><div class="inner">
    <div class="field"><label>Название</label><input type="text" data-ti="${i}" data-tf="name" value="${esc(t.name)}"></div>
    <div class="field"><label>Ключевые слова (через запятую; пусто = fallback)</label><input type="text" data-ti="${i}" data-tf="keywords" value="${esc(t.keywords)}"></div>
    <div class="field"><label>Текст письма</label><textarea rows="6" data-ti="${i}" data-tf="text">${esc(t.text)}</textarea></div>
    <div class="row"><button class="sec" onclick="tplMove(${i},-1)">↑</button><button class="sec" onclick="tplMove(${i},1)">↓</button><button class="sec" onclick="tplCopy(${i})">Копия</button><button class="danger" onclick="tplDel(${i})">Удалить</button></div>
  </div></details>`;
}
function tplSync(){document.querySelectorAll('#tpls [data-ti]').forEach(el=>{const i=+el.dataset.ti,f=el.dataset.tf;if(TPLS[i])TPLS[i][f]=el.value;});}
function tplAdd(){tplSync();TPLS.push({name:'Новый',keywords:'',text:''});renderTemplates();}
function tplDel(i){tplSync();TPLS.splice(i,1);renderTemplates();}
function tplCopy(i){tplSync();const c=Object.assign({},TPLS[i]);c.name=(c.name||'')+' (копия)';TPLS.splice(i+1,0,c);renderTemplates();}
function tplMove(i,d){tplSync();const j=i+d;if(j<0||j>=TPLS.length)return;const x=TPLS[i];TPLS[i]=TPLS[j];TPLS[j]=x;renderTemplates();}
async function saveTemplates(){
  tplSync();
  const r=await (await fetch('api/templates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({templates:TPLS})})).json();
  const m=$('#tpl-msg');m.textContent=r.ok?'✅ сохранено':('❌ '+(r.error||'ошибка'));m.style.color=r.ok?'#22c55e':'#fca5a5';
}

// ---- Расписание ---- #
async function openSchedule(){
  CURRENT={type:'schedule'};
  const s=await (await fetch('api/schedule')).json();
  const cmd=(s.argv||['apply-vacancies','--ai']).join(' ');
  const c=$('#content');
  c.innerHTML=`<h2>Расписание</h2><p class="hint">Раз в сутки в указанное время панель сама запустит команду. Работает, пока запущен контейнер панели (у него <code>restart: unless-stopped</code>). Время — локальное (как на хосте). Если ПК/сервер выключен в это время — запуск пропускается.</p>
  <div class="field"><label class="chk"><input type="checkbox" id="sch-en" ${s.enabled?'checked':''}> <span>Включить ежедневный запуск</span></label></div>
  <div class="grid">
    <div class="field"><label>Время (ЧЧ:ММ)</label><input type="time" id="sch-time" value="${esc(s.time||'09:00')}"></div>
    <div class="field"><label>Последний запуск</label><input type="text" value="${esc(s.last_run_date||'—')}" disabled></div>
  </div>
  <div class="field"><label>Команда (аргументы CLI, как в «Произвольная команда»)</label><input type="text" id="sch-cmd" value="${esc(cmd)}"></div>
  <p class="hint">Пример для откликов: <code>apply-vacancies --ai --ai-filter light --search "React OR Frontend"</code>. Совет: сначала проверьте её вручную с <code>--dry-run</code>.</p>
  <div class="row"><button onclick="saveSchedule()">💾 Сохранить</button><button class="sec" onclick="runScheduleNow()">▶ Запустить сейчас</button><span id="sch-msg" class="fh"></span></div>`;
  setActive(-1);
}
function scheduleArgv(){return splitArgs($('#sch-cmd').value.trim());}
async function saveSchedule(){
  const body={enabled:$('#sch-en').checked, time:$('#sch-time').value, argv:scheduleArgv()};
  const r=await (await fetch('api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  const m=$('#sch-msg');m.textContent=r.ok?'✅ сохранено':('❌ '+(r.error||'ошибка'));m.style.color=r.ok?'#22c55e':'#fca5a5';
}
async function runScheduleNow(){const a=scheduleArgv();if(a.length)await run(a);}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// ---- Ресайз терминала перетаскиванием ---- #
(function(){
  const handle=document.getElementById('conresize'), con=document.getElementById('console');
  const saved=parseInt(localStorage.getItem('con_h')||'0');
  if(saved>80)con.style.height=saved+'px';
  let sy=0,sh=0;
  function clamp(h){return Math.max(80,Math.min(window.innerHeight-120,h));}
  function move(e){con.style.height=clamp(sh+(sy-e.clientY))+'px';}
  function up(){handle.classList.remove('drag');document.body.style.userSelect='';document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up);localStorage.setItem('con_h',parseInt(con.getBoundingClientRect().height));}
  handle.addEventListener('mousedown',e=>{sy=e.clientY;sh=con.getBoundingClientRect().height;handle.classList.add('drag');document.body.style.userSelect='none';document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);e.preventDefault();});
})();

boot();
</script>
</body>
</html>
"""
