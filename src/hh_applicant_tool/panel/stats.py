"""Профиль и статистика откликов прямо из API hh.

Своей базы у апстрима больше нет, поэтому считаем по /negotiations и
кладём сводку в panel.json, чтобы экран открывался мгновенно.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .store import PanelStore

if TYPE_CHECKING:
    from .hh import HHService

DAYS = 30
RECENT_LIMIT = 30
# Чтобы не ходить по сотням страниц: на сводку хватает последних откликов
MAX_NEGOTIATIONS = 2000


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # hh отдаёт 2026-11-05T12:34:56+0300
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


class Stats:
    def __init__(self, store: PanelStore, service: "HHService") -> None:
        self.store = store
        self.service = service
        self.key = f"stats:{service.profile}"
        self._lock = threading.Lock()

    def cached(self) -> dict[str, Any] | None:
        return self.store.get(self.key)

    def profile(self) -> dict[str, Any]:
        with self.service.lock:
            tool = self.service.tool()
            if not tool.token_config.get("token"):
                return {"logged_in": False}
            try:
                me = tool.get_me()
                resumes = tool.get_resumes()
            except Exception as ex:  # noqa: BLE001
                return {"logged_in": True, "error": str(ex)}
        return {
            "logged_in": True,
            "me": {
                "id": me.get("id"),
                "first_name": me.get("first_name"),
                "last_name": me.get("last_name"),
                "email": me.get("email"),
                "is_applicant": me.get("is_applicant", me.get("auth_type") == "applicant"),
                "counters": me.get("counters") or {},
            },
            "resumes": [
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "url": r.get("alternate_url"),
                    "status": (r.get("status") or {}).get("name"),
                    "updated_at": r.get("updated_at"),
                    "can_publish_or_update": r.get("can_publish_or_update"),
                    "next_publish_at": r.get("next_publish_at"),
                    "views": (r.get("counters") or {}).get("total_views")
                    if isinstance(r.get("counters"), dict)
                    else None,
                    "new_views": (r.get("counters") or {}).get("new_views")
                    if isinstance(r.get("counters"), dict)
                    else None,
                }
                for r in resumes
            ],
        }

    def refresh(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Статистика уже обновляется")
        try:
            return self._refresh()
        finally:
            self._lock.release()

    def _refresh(self) -> dict[str, Any]:
        return self._collect()

    def _pages(self):
        """Отклики постранично. Общую блокировку берём на одну страницу,
        чтобы долгий пересчёт не подвешивал остальные экраны панели."""
        page = 0
        while True:
            with self.service.lock:
                r = self.service.tool().api_client.get(
                    "/negotiations", page=page, per_page=100, status="all"
                )
            items = r.get("items", [])
            if page == 0:
                self._found = r.get("found")
            yield from items
            page += 1
            if not items or page >= r.get("pages", 0):
                return

    def _collect(self) -> dict[str, Any]:
        with self.service.lock:
            tool = self.service.tool()
            if not tool.token_config.get("token"):
                raise RuntimeError("Сначала войдите в аккаунт")
        self._found = None
        by_state: Counter[str] = Counter()
        state_names: dict[str, str] = {}
        per_day: dict[str, Counter[str]] = defaultdict(Counter)
        by_resume: Counter[str] = Counter()
        resume_titles: dict[str, str] = {}
        viewed = 0
        total = 0
        recent = []
        since = datetime.now(timezone.utc) - timedelta(days=DAYS)
        last_created: datetime | None = None
        for n in self._pages():
            total += 1
            state = n.get("state") or {}
            sid = state.get("id") or "unknown"
            by_state[sid] += 1
            state_names.setdefault(sid, state.get("name") or sid)
            if n.get("viewed_by_opponent"):
                viewed += 1
            resume = n.get("resume") or {}
            if resume.get("id"):
                by_resume[resume["id"]] += 1
                resume_titles[resume["id"]] = resume.get("title") or resume["id"]
            created = _parse_dt(n.get("created_at"))
            if created and (last_created is None or created > last_created):
                last_created = created
            if created and created >= since:
                per_day[created.date().isoformat()][sid] += 1
            if len(recent) < RECENT_LIMIT:
                vacancy = n.get("vacancy") or {}
                recent.append(
                    {
                        "vacancy": vacancy.get("name"),
                        "url": vacancy.get("alternate_url"),
                        "employer": (vacancy.get("employer") or {}).get("name"),
                        "state": sid,
                        "state_name": state.get("name") or sid,
                        "viewed": bool(n.get("viewed_by_opponent")),
                        "has_updates": bool(n.get("has_updates")),
                        "updated_at": n.get("updated_at"),
                    }
                )
            if total >= MAX_NEGOTIATIONS:
                break

        today = datetime.now().date()
        days = []
        for i in range(DAYS - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            days.append({"date": d, "by_state": dict(per_day.get(d, {}))})

        # Реальное число откликов: сводку считаем по последним MAX_NEGOTIATIONS
        found = self._found or total
        data = {
            "updated_at": time.time(),
            "found": found,
            "total": total,
            "truncated": total >= MAX_NEGOTIATIONS,
            "last_created_at": last_created.timestamp() if last_created else None,
            "viewed": viewed,
            "by_state": [
                {"id": k, "name": state_names[k], "count": v}
                for k, v in by_state.most_common()
            ],
            "days": days,
            "by_resume": [
                {"id": k, "title": resume_titles[k], "count": v}
                for k, v in by_resume.most_common()
            ],
            "recent": recent,
        }
        self.store.set(self.key, data)
        return data
