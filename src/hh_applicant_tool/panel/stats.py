"""Профиль и статистика откликов прямо из API hh.

Своей базы у апстрима больше нет, поэтому считаем по /negotiations и
кладём сводку в panel.json, чтобы экран открывался мгновенно.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..tool import HHApplicantTool
from .store import PanelStore

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
    def __init__(self, store: PanelStore, config_dir: Path) -> None:
        self.store = store
        self.config_dir = config_dir
        self._lock = threading.Lock()

    def _tool(self) -> HHApplicantTool:
        # Капчу здесь решать некому: панель не должна зависнуть на input()
        return HHApplicantTool(config_dir=self.config_dir, captcha_handling=False)

    def cached(self) -> dict[str, Any] | None:
        return self.store.get("stats")

    def profile(self) -> dict[str, Any]:
        tool = self._tool()
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
        tool = self._tool()
        if not tool.token_config.get("token"):
            raise RuntimeError("Сначала войдите в аккаунт")
        by_state: Counter[str] = Counter()
        state_names: dict[str, str] = {}
        per_day: dict[str, Counter[str]] = defaultdict(Counter)
        by_resume: Counter[str] = Counter()
        resume_titles: dict[str, str] = {}
        viewed = 0
        total = 0
        recent = []
        since = datetime.now(timezone.utc) - timedelta(days=DAYS)
        for n in tool.get_negotiations(status="all"):
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

        data = {
            "updated_at": time.time(),
            "total": total,
            "truncated": total >= MAX_NEGOTIATIONS,
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
        self.store.set("stats", data)
        return data
