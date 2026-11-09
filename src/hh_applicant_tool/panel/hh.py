"""Работа с hh.ru прямо из процесса панели.

Логику не пишем заново, а берём из операций утилиты. У каждой операции
run() сначала всё настраивает (конфиг, промпты, AI-клиенты, фильтры), а в
конце вызывает один метод, который делает работу. Мы подменяем этот метод
заглушкой, вызываем run() и получаем настроенный объект, у которого можно
дёргать отдельные шаги: найти вакансии, проверить AI-фильтром, собрать
письмо, откликнуться, загрузить чат, попросить AI ответ.

Капчу, которая всплывает при таких запросах, решает AI (если настроен),
иначе вопрос уходит в браузер через CaptchaBroker.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ai.base import AIError
from ..api.errors import ApiError
from ..errors import OperationError
from ..operations import apply_vacancies as av
from ..operations import clear_negotiations as cn
from ..operations import reply_chats as rc
from ..tool import BaseNamespace, HHApplicantTool
from ..utils.string import strip_tags

logger = logging.getLogger(__name__)

CAPTCHA_TIMEOUT = 300


class CaptchaBroker:
    """Очередь капч, которые ждут ответа человека в браузере."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def ask(self, image: bytes) -> str:
        item_id = uuid.uuid4().hex[:10]
        event = threading.Event()
        item = {"id": item_id, "image": base64.b64encode(image).decode(), "event": event, "answer": None, "created_at": time.time()}
        with self._lock:
            self._items[item_id] = item
        try:
            if not event.wait(CAPTCHA_TIMEOUT):
                raise OperationError("Капчу не ввели за 5 минут")
            return item["answer"] or ""
        finally:
            with self._lock:
                self._items.pop(item_id, None)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": i["id"], "image": i["image"], "created_at": i["created_at"]}
                for i in self._items.values()
            ]

    def answer(self, item_id: str, text: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
        if not item:
            return False
        item["answer"] = text.strip()
        item["event"].set()
        return True


class PanelTool(HHApplicantTool):
    """Утилита, у которой ручной ввод капчи идёт через браузер."""

    broker: CaptchaBroker

    def _can_use_tkinter(self) -> bool:
        return False

    def _prompt_captcha_in_terminal(self, image_data: bytes) -> str:
        return self.broker.ask(image_data)


def _ns(parser_args: list[str]) -> BaseNamespace:
    """Аргументы операции так же, как их разобрал бы CLI."""
    parser = HHApplicantTool._create_parser()
    return parser.parse_args(parser_args, namespace=BaseNamespace())


def _short_vacancy(v: dict[str, Any]) -> dict[str, Any]:
    salary = v.get("salary") or {}
    snippet = v.get("snippet") or {}
    employer = v.get("employer") or {}
    return {
        "id": v.get("id"),
        "name": v.get("name"),
        "url": v.get("alternate_url"),
        "employer": employer.get("name"),
        "employer_id": employer.get("id"),
        "area": (v.get("area") or {}).get("name"),
        "salary": {"from": salary.get("from"), "to": salary.get("to"), "currency": salary.get("currency")} if salary else None,
        "requirement": strip_tags(snippet.get("requirement") or ""),
        "responsibility": strip_tags(snippet.get("responsibility") or ""),
        "published_at": v.get("published_at"),
        "has_test": bool(v.get("has_test")),
        "response_letter_required": bool(v.get("response_letter_required")),
        "relations": v.get("relations") or [],
        "archived": bool(v.get("archived")),
        "response_url": v.get("response_url"),
        "schedule": (v.get("schedule") or {}).get("name"),
        "experience": (v.get("experience") or {}).get("name"),
    }


class HHService:
    def __init__(self, base_dir: Path, profile: str, broker: CaptchaBroker) -> None:
        self.base_dir = base_dir
        self.profile = profile
        self.broker = broker
        # requests.Session не потокобезопасна: все обращения по очереди
        self.lock = threading.RLock()
        self._tool: PanelTool | None = None
        self._chat_op: rc.Operation | None = None
        self._chat_cache: dict[int, rc.ProcessingChat] = {}

    # ---- инструмент ----------------------------------------------------

    def tool(self) -> PanelTool:
        if self._tool is None:
            tool = PanelTool(
                config_dir=self.base_dir,
                profile_id=None if self.profile == "." else self.profile,
                use_ai=True,
            )
            tool.broker = self.broker
            self._tool = tool
        return self._tool

    def reset(self) -> None:
        """Сбросить кэш после смены конфига или входа в аккаунт."""
        with self.lock:
            if self._tool is not None:
                try:
                    self._tool.save_cookies()
                except Exception:  # noqa: BLE001
                    pass
            self._tool = None
            self._chat_op = None
            self._chat_cache.clear()

    def logged_in(self) -> bool:
        return bool(self.tool().token_config.get("token"))

    def _require_login(self) -> PanelTool:
        tool = self.tool()
        if not tool.token_config.get("token"):
            raise RuntimeError("Сначала войдите в аккаунт hh.ru")
        return tool

    def _done(self) -> None:
        try:
            self.tool().save_cookies()
        except Exception:  # noqa: BLE001
            pass

    # ---- профиль и резюме ---------------------------------------------

    def resumes(self) -> list[dict[str, Any]]:
        with self.lock:
            tool = self._require_login()
            items = tool.get_resumes()
        out = []
        for r in items:
            # Счётчики hh кладёт прямо в резюме; counters встречается в
            # старых ответах, оставляем как запасной вариант
            counters = r.get("counters") if isinstance(r.get("counters"), dict) else {}
            similar = (r.get("similar_vacancies") or {}).get("counters") or {}
            status = (r.get("status") or {}).get("name") or ""
            salary = r.get("salary") or {}
            out.append(
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "url": r.get("alternate_url"),
                    "status": status[:1].upper() + status[1:],
                    "status_id": (r.get("status") or {}).get("id"),
                    "updated_at": r.get("updated_at"),
                    "can_publish_or_update": r.get("can_publish_or_update"),
                    "next_publish_at": r.get("next_publish_at"),
                    "total_views": r.get("total_views", counters.get("total_views")),
                    "new_views": r.get("new_views", counters.get("new_views")),
                    "similar": similar.get("total"),
                    "salary": salary or None,
                    "area": (r.get("area") or {}).get("name"),
                    "access": (r.get("access") or {}).get("type", {}).get("name") if isinstance((r.get("access") or {}).get("type"), dict) else None,
                }
            )
        return out

    def resume_ready_at(self) -> datetime | None:
        """Когда hh разрешит поднять хотя бы одно опубликованное резюме
        (локальное время без пояса). None: опубликованных резюме нет."""
        times = []
        for r in self.resumes():
            if r["status_id"] != "published":
                continue
            if r["can_publish_or_update"]:
                return datetime.now()
            if r["next_publish_at"]:
                times.append(
                    datetime.strptime(r["next_publish_at"], "%Y-%m-%dT%H:%M:%S%z")
                    .astimezone()
                    .replace(tzinfo=None)
                )
        return min(times, default=None)

    def resume(self, resume_id: str) -> dict[str, Any]:
        with self.lock:
            tool = self._require_login()
            data = tool.api_client.get(f"/resumes/{resume_id}")
            try:
                placeholders = tool.get_resume_placeholders(resume_id)
            except Exception:  # noqa: BLE001
                placeholders = {}
            self._done()
        return {"resume": data, "placeholders": placeholders}

    def publish_resume(self, resume_id: str) -> None:
        with self.lock:
            tool = self._require_login()
            tool.api_client.post(f"/resumes/{resume_id}/publish")
            self._done()

    # Поля, которые панель даёт менять. Остальное правится на сайте
    RESUME_EDITABLE = ("title", "salary", "skill_set", "skills")

    def update_resume(self, resume_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not title:
                raise ValueError("Укажите желаемую должность")
            payload["title"] = title
        if "salary" in fields:
            s = fields["salary"]
            if s in (None, "", {}):
                payload["salary"] = None
            else:
                amount = int(s.get("amount") or 0) if isinstance(s, dict) else int(s)
                currency = (s.get("currency") if isinstance(s, dict) else None) or "RUR"
                payload["salary"] = {"amount": amount, "currency": currency} if amount > 0 else None
        if "skill_set" in fields:
            skills = [str(x).strip() for x in fields["skill_set"] or [] if str(x).strip()]
            payload["skill_set"] = list(dict.fromkeys(skills))
        if "skills" in fields:
            payload["skills"] = str(fields["skills"] or "")
        if not payload:
            raise ValueError("Нечего сохранять")
        with self.lock:
            tool = self._require_login()
            tool.api_client.put(f"/resumes/{resume_id}", payload)
            self._done()
        return self.resume(resume_id)

    def resume_views(self, resume_id: str) -> list[dict[str, Any]]:
        with self.lock:
            tool = self._require_login()
            data = tool.api_client.get(f"/resumes/{resume_id}/views", per_page=50)
        return [
            {
                "employer": (i.get("employer") or {}).get("name"),
                "employer_url": (i.get("employer") or {}).get("alternate_url"),
                "created_at": i.get("created_at"),
                "viewed": i.get("viewed"),
            }
            for i in data.get("items", [])
        ]

    def skill_suggest(self, text: str) -> list[str]:
        with self.lock:
            tool = self._require_login()
            data = tool.api_client.get("/suggests/skill_set", text=text)
        return [i.get("text") for i in data.get("items", []) if i.get("text")]

    # ---- чаты ----------------------------------------------------------

    def _chats(self) -> rc.Operation:
        if self._chat_op is None:
            tool = self._require_login()
            op = rc.Operation()
            op.reply_chats = lambda: None  # type: ignore[method-assign]
            op.run(tool, _ns(["reply-chats", "--dry-run"]))
            self._chat_op = op
        return self._chat_op

    def chats(self, from_key: str | None = None, unread: bool = False) -> dict[str, Any]:
        with self.lock:
            op = self._chats()
            data = op.get_chats(from_key=from_key, refer_url=op.chat_page_url, unread=unread)
            unread_count = op.get_unread_count().get("unreadCount", 0)
            self._done()
        chats = data.get("chats") or {}
        resources = data.get("resources") or {}
        display = data.get("chatsDisplayInfo") or {}
        items = []
        for item in chats.get("items") or []:
            res = item.get("resources") or {}
            vacancy = op.get_vacancy(res, resources) or {}
            resume_id = op.get_resume_id(res, resources)
            last = item.get("lastMessage") or {}
            me = str(item.get("currentParticipantId") or "")
            topic_refs = res.get("NEGOTIATION_TOPIC") or []
            topic = (resources.get("negotiation_topics") or {}).get(topic_refs[0]) if topic_refs else None
            state = (topic or {}).get("currentApplicantState", "")
            info = display.get(str(item["id"])) or {}
            if last and vacancy and resume_id:
                self._chat_cache[int(item["id"])] = rc.ProcessingChat(
                    chat_id=int(item["id"]),
                    resume_id=resume_id,
                    current_participant_id=me,
                    vacancy=vacancy,
                    applicant_state=state,
                    last_message=last,
                    last_is_mine=str(last.get("participantId") or "") == me,
                    viewed_by_me=last.get("id") == item.get("lastViewedByCurrentUserMessageId"),
                )
            items.append(
                {
                    "id": item["id"],
                    "title": info.get("title") or vacancy.get("name") or "Чат",
                    "subtitle": info.get("subtitle") or (vacancy.get("company") or {}).get("name"),
                    "vacancy": vacancy.get("name"),
                    "vacancy_url": (vacancy.get("links") or {}).get("desktop"),
                    "unread": item.get("unreadCount") or 0,
                    "state": state,
                    "last_text": (last.get("text") or "").strip(),
                    "last_is_mine": str(last.get("participantId") or "") == me,
                    "last_at": last.get("creationTime"),
                    "can_ai": bool(last and vacancy and resume_id),
                }
            )
        return {"items": items, "next": chats.get("nextFrom"), "found": chats.get("found"), "unread": unread_count}

    def chat_messages(self, chat_id: int) -> dict[str, Any]:
        with self.lock:
            op = self._chats()
            data = op._xhr(
                "GET",
                "/chatik/api/chat_data",
                params={"chatId": chat_id, "do_not_track_session_events": "true"},
                referer_url=op.chat_referer(chat_id),
            )
            chat = data.get("chat") or {}
            me = str(chat.get("currentParticipantId") or "")
            messages = (chat.get("messages") or {}).get("items") or []
            writable = (data.get("chatStates") or {}).get("writeMessageState", {}).get("allowed", True)
            quick: list[str] = []
            if messages:
                last = messages[-1]
                try:
                    quick = op.get_quick_replies(chat_id, last["id"])
                except Exception:  # noqa: BLE001
                    quick = []
                if last.get("id") != chat.get("lastViewedByCurrentUserMessageId"):
                    try:
                        op.mark_chat_read(chat_id, last["id"])
                    except Exception:  # noqa: BLE001
                        pass
            self._done()
        return {
            "id": chat_id,
            "writable": writable,
            "has_more": (chat.get("messages") or {}).get("hasMore", False),
            "quick_replies": quick,
            "options": op.get_reply_options(messages[-1]) if messages else [],
            "messages": [
                {
                    "id": m.get("id"),
                    "text": m.get("text") or "",
                    "mine": str(m.get("participantId") or "") == me,
                    "author": (m.get("participantDisplay") or {}).get("name"),
                    "is_bot": bool((m.get("participantDisplay") or {}).get("isBot")),
                    "at": m.get("creationTime"),
                }
                for m in messages
            ],
        }

    def chat_send(self, chat_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("Пустое сообщение")
        with self.lock:
            op = self._chats()
            op.notify_chat_opened(chat_id)
            op.start_chat_typing(chat_id)
            op.send_chat_message(chat_id, text, refer_url=op.chat_referer(chat_id))
            self._done()

    def chat_leave(self, chat_id: int) -> None:
        with self.lock:
            op = self._chats()
            op.leave_chat(chat_id, refer_url=op.chat_referer(chat_id))
            self._done()

    def chat_ai_draft(self, chat_id: int) -> str:
        with self.lock:
            op = self._chats()
            if not op.ai_enabled:
                raise RuntimeError("AI не настроен. Укажите модель в разделе «Модели»")
            chat = self._chat_cache.get(chat_id)
            if chat is None:
                raise RuntimeError("Обновите список чатов и попробуйте снова")
            text = op.get_ai_reply(chat, use_options=False)
            self._done()
        if not text:
            raise RuntimeError("Модель не предложила ответ")
        return text

    # ---- вакансии --------------------------------------------------------

    def _apply_op(self, args: list[str]) -> av.Operation:
        tool = self._require_login()
        op = av.Operation()
        op._apply_vacancies = lambda: None  # type: ignore[method-assign]
        op.run(tool, _ns(["apply-vacancies", *args]))
        return op

    def search(self, args: list[str], resume_id: str, page: int = 0) -> dict[str, Any]:
        with self.lock:
            op = self._apply_op(args)
            params = op._get_search_params(page)
            if op.text:
                endpoint = "/vacancies"
                params |= {"text": op.text}
            else:
                endpoint = f"/resumes/{resume_id}/similar_vacancies"
            params["per_page"] = min(int(params.get("per_page") or 20), 50)
            res = op.api_client.get(endpoint, params)
            self._done()
        items = []
        for v in res.get("items", []):
            short = _short_vacancy(v)
            short["skip_reason"] = self._skip_reason(op, v)
            items.append(short)
        return {"items": items, "found": res.get("found"), "pages": res.get("pages"), "page": page}

    @staticmethod
    def _skip_reason(op: av.Operation, v: dict[str, Any]) -> str | None:
        rel = v.get("relations") or []
        if "got_rejection" in rel:
            return "Уже был отказ"
        if rel:
            return "Уже откликались"
        if v.get("archived"):
            return "В архиве"
        if v.get("response_url"):
            return "Отклик на сайте работодателя"
        if v.get("has_test") and op.skip_tests:
            return "Есть тест"
        return None

    def _placeholders(self, op: av.Operation, resume_id: str, vacancy: dict[str, Any]) -> dict[str, Any]:
        tool = op.tool
        resume = next((r for r in tool.get_resumes() if r["id"] == resume_id), None)
        if resume is None:
            raise RuntimeError("Резюме не найдено")
        base = op._build_resume_placeholders(resume, tool.get_me())
        employer = vacancy.get("employer") or {}
        return {
            "vacancy_name": vacancy.get("name", ""),
            "vacancy_url": vacancy.get("alternate_url") or "",
            "employer_name": employer.get("name", ""),
            **base,
        }

    def vacancy_check(self, args: list[str], resume_id: str, vacancy_id: str) -> dict[str, Any]:
        """Прогон одной вакансии через фильтры и сборку письма, без отклика."""
        with self.lock:
            op = self._apply_op([*args, "--ai-filter"]) if "--ai-filter" not in args else self._apply_op(args)
            vacancy = op.api_client.get(f"/vacancies/{vacancy_id}")
            ph = self._placeholders(op, resume_id, vacancy)
            excluded = op._is_excluded(vacancy) if op.excluded_pat else False
            suitable = None
            ai_error = None
            if op.vacancy_filter_ai:
                try:
                    suitable = op._is_vacancy_suitable(vacancy, ph)
                except AIError as ex:
                    # Модель недоступна: не роняем проверку, а честно
                    # говорим, что фильтр не сработал
                    ai_error = str(ex)
            force = op.force_cover_letter
            op.force_cover_letter = True
            try:
                letter = op._build_cover_letter(vacancy, ph)
            except AIError as ex:
                ai_error = ai_error or str(ex)
                op.cover_letter_ai = None
                letter = op._build_cover_letter(vacancy, ph)
            op.force_cover_letter = force
            template = None
            if op.letter_template:
                template = op.letter_template.get("name")
            elif not op.cover_letter_ai:
                t = op._match_letter_template(vacancy)
                template = t.get("name") if t else None
            self._done()
        return {
            "excluded": excluded,
            "suitable": suitable,
            "ai_error": ai_error,
            "letter": letter,
            "letter_source": "template" if template else ("ai" if op.cover_letter_ai else "default"),
            "template": template,
            "description": strip_tags(vacancy.get("description") or "")[:4000],
            "key_skills": [s.get("name") for s in vacancy.get("key_skills") or [] if s.get("name")],
        }

    def apply(self, resume_id: str, vacancy_id: str, letter: str) -> None:
        with self.lock:
            tool = self._require_login()
            res = tool.api_client.post(
                "/negotiations",
                {"resume_id": resume_id, "vacancy_id": vacancy_id, "message": letter},
            )
            self._done()
        if res != {}:
            raise RuntimeError(f"hh ответил неожиданно: {str(res)[:200]}")

    def blacklist_vacancy(self, vacancy_id: str, on: bool = True) -> None:
        with self.lock:
            tool = self._require_login()
            if on:
                tool.api_client.put(f"/vacancies/blacklisted/{vacancy_id}")
            else:
                tool.api_client.delete(f"/vacancies/blacklisted/{vacancy_id}")
            self._done()

    def blacklist_employer(self, employer_id: str, on: bool = True) -> None:
        with self.lock:
            tool = self._require_login()
            if on:
                tool.api_client.put(f"/employers/blacklisted/{employer_id}")
            else:
                tool.api_client.delete(f"/employers/blacklisted/{employer_id}")
            self._done()

    def blacklisted(self, kind: str, page: int = 0) -> dict[str, Any]:
        endpoint = {"vacancies": "/vacancies/blacklisted", "employers": "/employers/blacklisted"}[kind]
        with self.lock:
            tool = self._require_login()
            res = tool.api_client.get(endpoint, page=page, per_page=50)
            self._done()
        items = []
        for i in res.get("items", []):
            if kind == "vacancies":
                items.append({"id": i.get("id"), "name": i.get("name"), "url": i.get("alternate_url"), "employer": (i.get("employer") or {}).get("name")})
            else:
                items.append({"id": i.get("id"), "name": i.get("name"), "url": i.get("alternate_url"), "vacancies": i.get("open_vacancies")})
        return {"items": items, "found": res.get("found"), "pages": res.get("pages"), "page": page}

    # ---- отклики ------------------------------------------------------

    def negotiations(self, status: str = "all", page: int = 0) -> dict[str, Any]:
        with self.lock:
            tool = self._require_login()
            res = tool.api_client.get("/negotiations", page=page, per_page=50, status=status, order_by="updated_at")
            self._done()
        items = []
        for n in res.get("items", []):
            vacancy = n.get("vacancy") or {}
            employer = vacancy.get("employer") or {}
            items.append(
                {
                    "id": n.get("id"),
                    "state": (n.get("state") or {}).get("id"),
                    "state_name": (n.get("state") or {}).get("name"),
                    "created_at": n.get("created_at"),
                    "updated_at": n.get("updated_at"),
                    "viewed": bool(n.get("viewed_by_opponent")),
                    "has_updates": bool(n.get("has_updates")),
                    "vacancy": vacancy.get("name"),
                    "vacancy_url": vacancy.get("alternate_url"),
                    "employer": employer.get("name"),
                    "employer_id": employer.get("id"),
                    "resume": (n.get("resume") or {}).get("title"),
                }
            )
        return {"items": items, "found": res.get("found"), "pages": res.get("pages"), "page": page}

    def negotiation_hide(self, negotiation_id: str, *, decline: bool, delete_chat: bool) -> None:
        with self.lock:
            tool = self._require_login()
            tool.api_client.delete(f"/negotiations/active/{negotiation_id}", with_decline_message=decline)
            if delete_chat:
                op = cn.Operation()
                op.tool = tool
                op.delete_chat(negotiation_id)
            self._done()

    # ---- служебное ------------------------------------------------------

    def read_log(self, limit: int = 400, level: str | None = None) -> dict[str, Any]:
        path = self.tool().log_file
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))
                data = f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return {"lines": [], "path": str(path), "size": 0}
        lines = data.splitlines()
        if size > 512 * 1024:
            lines = lines[1:]
        if level:
            lines = [ln for ln in lines if f" - {level} - " in ln]
        return {"lines": lines[-limit:], "path": str(path), "size": size}

    def clear_log(self) -> None:
        path = self.tool().log_file
        if path.exists():
            path.write_text("")


def friendly_error(ex: Exception) -> str:
    if isinstance(ex, ApiError):
        return f"hh.ru отказал: {ex}"
    return str(ex)


def parse_hh_dt(value: str | None) -> float | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
