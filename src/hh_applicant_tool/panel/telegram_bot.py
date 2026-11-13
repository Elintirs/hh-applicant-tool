"""Бот панели в Telegram: меню, управление и ответы работодателям.

Панель сама опрашивает Telegram (long polling), публичный адрес не нужен.
Бот слушает только chat_id из [telegram]: всё остальное молча игнорирует.
Необратимое (ответ работодателю, запуск задачи) только после кнопки
подтверждения.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .. import notify
from ..operations.apply_vacancies import JOURNAL_FILENAME
from ..operations.daily_summary import summarize
from ..utils.config import ConfigTOML
from .child import CAPTCHA_TAG

if TYPE_CHECKING:
    from .server import PanelApp

logger = logging.getLogger(__name__)

# Что ставим на паузу: отклики и автоответы. Поднятие резюме и чистку не трогаем
PAUSABLE = ("apply-vacancies", "reply-chats")
RUNS = {
    "apply": ("apply-vacancies", "Отклики на вакансии", ["apply-vacancies", "--ai-filter", "--max-responses=200"]),
    "chats": ("reply-chats", "Ответы в чатах", ["reply-chats", "--no-loop"]),
    "resume": ("update-resumes", "Поднять резюме", ["update-resumes"]),
}
COMMANDS = [
    ("menu", "Меню"),
    ("today", "Сводка за сегодня"),
    ("waiting", "Чаты, где ждут ответа"),
    ("status", "Расписание и что идёт сейчас"),
    ("pause", "Пауза откликов и автоответов"),
    ("resume", "Снять паузу"),
    ("run", "Запустить задачу"),
]
CHAT_RE = re.compile(r"chat/(\d+)")
CAPTCHA_RE = re.compile(re.escape(CAPTCHA_TAG) + r" ([0-9a-f]{32})")
DONE_KEEP = 300


class TelegramBot:
    def __init__(self, app: PanelApp) -> None:
        self.app = app
        self._stop = threading.Event()
        self._pending: dict[str, tuple[int, str]] = {}  # токен -> (чат hh, текст)
        self._commands_for = ""  # для какого токена уже выставлено меню

    # ---- запуск --------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._loop, name="telegram-bot", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _creds(self) -> tuple[str, str]:
        # Читаем каждый раз: токен могли поменять на вкладке «Уведомления»
        try:
            return notify.settings(ConfigTOML(self.app.ctx().settings.path))
        except Exception:  # noqa: BLE001
            return "", ""

    def _loop(self) -> None:
        while not self._stop.is_set():
            token, chat_id = self._creds()
            if not token or not chat_id:
                self._stop.wait(30)
                continue
            try:
                if self._commands_for != token:
                    notify.call(token, "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
                    self._commands_for = token
                offset = self.app.store.get("tg_offset") or 0
                data = notify.call(token, "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]}, timeout=40)
                for upd in data.get("result") or []:
                    self.app.store.set("tg_offset", upd["update_id"] + 1)
                    try:
                        self._handle(token, chat_id, upd)
                    except Exception:  # noqa: BLE001
                        logger.exception("telegram update failed")
            except RuntimeError as ex:
                logger.debug("telegram poll: %s", ex)
                self._stop.wait(15)

    # ---- разбор ----------------------------------------------------------

    def _handle(self, token: str, chat_id: str, upd: dict) -> None:
        cb = upd.get("callback_query")
        msg = upd.get("message") or (cb or {}).get("message") or {}
        sender = str(((cb or {}).get("from") or msg.get("chat") or {}).get("id") or "")
        # Приватность: кроме владельца никому не отвечаем, даже ошибкой
        if sender != chat_id or str((msg.get("chat") or {}).get("id")) != chat_id:
            return
        say = lambda text, buttons=None: notify.send_message(token, chat_id, text, buttons)  # noqa: E731
        if cb:
            try:
                notify.call(token, "answerCallbackQuery", {"callback_query_id": cb["id"]})
            except RuntimeError:
                pass
            self._callback(say, cb.get("data") or "")
            return
        text = (msg.get("text") or "").strip()
        original = msg.get("reply_to_message") or {}
        reply_to = original.get("text") or original.get("caption") or ""
        if reply_to and text and not text.startswith("/"):
            m = CAPTCHA_RE.search(reply_to)
            if m:
                say(self._captcha_answer(m.group(1), text))
                return
            m = CHAT_RE.search(reply_to)
            if m:
                self._confirm_send(say, int(m.group(1)), text)
                return
        cmd = text.split()[0].split("@")[0].lstrip("/").lower() if text.startswith("/") else ""
        self._command(say, cmd or "menu")

    def _command(self, say, cmd: str) -> None:
        if cmd in ("menu", "start"):
            say(self._status_line() + "\n\nЧто сделать?", [
                [("📊 Сегодня", "m:today"), ("🙋 Ждут ответа", "m:wait")],
                [("🗓 Статус", "m:status"), ("▶️ Запустить", "m:run")],
                [("⏸ Пауза", "m:pause") if not self._paused() else ("▶️ Снять паузу", "m:resume")],
            ])
        elif cmd == "today":
            say(self._today())
        elif cmd == "waiting":
            self._waiting(say)
        elif cmd == "status":
            say(self._status())
        elif cmd == "pause":
            say(self._pause())
        elif cmd == "resume":
            say(self._resume())
        elif cmd == "run":
            say("Что запустить?", [[(title, f"r:{k}")] for k, (_, title, _) in RUNS.items()])
        else:
            say("Не знаю такой команды. /menu — что умею")

    def _callback(self, say, data: str) -> None:
        kind, _, arg = data.partition(":")
        if kind == "m":
            self._command(say, {"wait": "waiting"}.get(arg, arg))
        elif kind == "r" and arg in RUNS:
            say(f"Запустить «{RUNS[arg][1]}»?", [[("Да, запустить", f"rk:{arg}"), ("Отмена", "m:menu")]])
        elif kind == "rk" and arg in RUNS:
            say(self._run(arg))
        elif kind == "d":
            self._draft(say, int(arg))
        elif kind == "x":
            self._mark_done(int(arg))
            say("✅ Отметил: этот чат больше не в списке ждущих")
        elif kind == "s":
            self._do_send(say, arg)
        elif kind == "c":
            self._pending.pop(arg, None)
            say("Отменил, ничего не отправлено")

    # ---- действия ----------------------------------------------------------

    def _captcha_answer(self, name: str, text: str) -> str:
        for p in self.app.jobs.prompts():
            if p.get("kind") == "captcha" and p.get("image") == f"{name}.png":
                if self.app.jobs.send_input(p["job_id"], text):
                    return "✅ Отправил капчу в задачу"
        return "Эта капча уже неактуальна: её ввели в панели или вышло время"

    def _journal(self, days: int = 3) -> list[dict]:
        path = self.app.ctx().dir / JOURNAL_FILENAME
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        out = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-3000:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if str(r.get("ts", "")) >= since:
                    out.append(r)
        return out

    def _today(self) -> str:
        return summarize(self._journal(1), date.today().isoformat()) or "За сегодня в журнале пусто"

    def _waiting_list(self) -> list[dict]:
        done = set(self.app.store.get("tg_done") or [])
        last: dict[Any, dict] = {}
        for r in self._journal(3):
            if r.get("kind") != "chat" or not r.get("chat_id"):
                continue
            last[r["chat_id"]] = r  # последнее решение по чату
        return [r for cid, r in last.items() if r.get("result") == "chat_manual" and cid not in done]

    def _waiting(self, say) -> None:
        items = self._waiting_list()
        if not items:
            say("Никто не ждёт ответа 🎉")
            return
        say(f"Ждут ответа: {len(items)}" + (", показываю последние 5" if len(items) > 5 else ""))
        for r in items[-5:]:
            say(
                f"🙋 {r.get('employer') or '?'} · {r.get('vacancy') or ''}\n«{(r.get('message') or '')[:300]}»\n\n{r.get('url')}\n↩️ Ответь на это сообщение, чтобы написать работодателю",
                [[("Открыть в hh", r.get("url") or "https://hh.ru")], [("✍️ Черновик AI", f"d:{r['chat_id']}"), ("✅ Готово", f"x:{r['chat_id']}")]],
            )

    def _mark_done(self, chat_id: int) -> None:
        done = [c for c in (self.app.store.get("tg_done") or []) if c != chat_id] + [chat_id]
        self.app.store.set("tg_done", done[-DONE_KEEP:])

    def _draft(self, say, hh_chat: int) -> None:
        say("Пишу черновик, это до минуты…")
        service = self.app.ctx().service
        try:
            service.chats()  # заполнить кэш чатов, без него черновик не собрать
            text = service.chat_ai_draft(hh_chat)
        except Exception as ex:  # noqa: BLE001
            say(f"Не получилось: {ex}")
            return
        self._confirm_send(say, hh_chat, text, title="Черновик AI")

    def _confirm_send(self, say, hh_chat: int, text: str, title: str = "Ответ работодателю") -> None:
        tok = secrets.token_hex(4)
        self._pending[tok] = (hh_chat, text)
        say(f"{title}:\n\n{text}\n\nОтправить в hh? Если нужно поправить, ответь на уведомление своим текстом.",
            [[("📤 Отправить в hh", f"s:{tok}"), ("Отмена", f"c:{tok}")]])

    def _do_send(self, say, tok: str) -> None:
        item = self._pending.pop(tok, None)
        if not item:
            say("Этот черновик устарел, собери ответ заново")
            return
        hh_chat, text = item
        try:
            self.app.ctx().service.chat_send(hh_chat, text)
        except Exception as ex:  # noqa: BLE001
            self._pending[tok] = item
            say(f"Не отправилось: {ex}")
            return
        self._mark_done(hh_chat)
        say("📨 Отправлено в hh")

    def _schedule(self) -> list[dict]:
        return self.app.scheduler.list()

    def _paused(self) -> bool:
        return bool(self.app.store.get("tg_paused"))

    def _pause(self) -> str:
        if self._paused():
            return "Уже на паузе. /resume — продолжить"
        items = self.app.store.get("schedule", [])
        ids = [i["id"] for i in items if i.get("enabled") and i["args"][:1] and i["args"][0] in PAUSABLE]
        for i in items:
            if i["id"] in ids:
                i["enabled"] = False
        self.app.scheduler.save(items)
        self.app.store.set("tg_paused", ids)
        return f"⏸ Пауза: отключил {len(ids)} задач (отклики и автоответы). Поднятие резюме и чистка работают. /resume — продолжить"

    def _resume(self) -> str:
        ids = set(self.app.store.get("tg_paused") or [])
        if not ids:
            return "Паузы нет, всё и так работает"
        items = self.app.store.get("schedule", [])
        for i in items:
            if i["id"] in ids:
                i["enabled"] = True
        self.app.scheduler.save(items)
        self.app.store.set("tg_paused", [])
        return f"▶️ Вернул {len(ids)} задач в расписание"

    def _run(self, key: str) -> str:
        cmd, title, default = RUNS[key]
        # Те же аргументы, что в расписании, чтобы не расходились настройки
        item = next((i for i in self.app.store.get("schedule", []) if i["args"][:1] == [cmd]), None)
        args = item["args"] if item else default
        if self.app.jobs.is_running(lambda j: bool(j.args) and j.args[0] == cmd):
            return f"«{title}» уже идёт"
        job = self.app.jobs.submit(args, source="telegram", title=title, profile=self.app.profile)
        return f"▶️ Запустил «{title}». Итог придёт сюда, если задача упадёт; ход виден в панели (задача {job.id})"

    def _status_line(self) -> str:
        waiting = len(self._waiting_list())
        return ("⏸ На паузе" if self._paused() else "✅ Работает") + (f" · ждут ответа: {waiting}" if waiting else "")

    def _status(self) -> str:
        lines = [self._status_line(), ""]
        running = [j for j in self.app.jobs.list() if j.get("status") in ("running", "queued")]
        if running:
            lines.append("Сейчас идёт: " + ", ".join(j.get("title") or j["args"][0] for j in running))
        for i in self._schedule():
            nxt = i.get("next_run_at")
            when = datetime.fromtimestamp(nxt).strftime("%d.%m %H:%M") if nxt else ("по hh" if i.get("mode") == "resume" else "—")
            lines.append(f"{'🟢' if i.get('enabled') else '⚪️'} {i['name']}: {when}")
        return "\n".join(lines)
