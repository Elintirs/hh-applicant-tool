from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING

from .. import notify
from ..tool import BaseNamespace, BaseOperation
from .apply_vacancies import JOURNAL_FILENAME

if TYPE_CHECKING:
    from ..tool import HHApplicantTool

logger = logging.getLogger(__name__.split(".")[0])


class Namespace(BaseNamespace):
    dry_run: bool


def summarize(records: list[dict], day: str) -> str | None:
    """Текст сводки по записям журнала за day (YYYY-MM-DD). None, если за
    день ничего не было: пустую сводку не шлём."""
    today = [r for r in records if str(r.get("ts", "")).startswith(day)]
    if not today:
        return None
    c = Counter(r.get("result") for r in today)
    # Чат, ждущий ответа, остаётся непрочитанным и попадает в журнал при
    # каждом запуске: считаем уникальные чаты
    manual = list({r.get("chat_id") or r.get("url"): r for r in today if r.get("result") == "chat_manual"}.values())
    lines = [f"📊 Сводка за {day[8:10]}.{day[5:7]}"]
    applied = c["sent"]
    if applied or c["ai_rejected"] or c["error"]:
        lines.append(f"Отклики: {applied} отправлено, {c['ai_rejected']} отсеял фильтр" + (f", {c['error']} ошибок" if c["error"] else ""))
    if c["limit"]:
        lines.append("Упёрлись в дневной лимит hh")
    chats = c["chat_sent"] + c["chat_left"]
    if chats or manual:
        lines.append(f"Чаты: {c['chat_sent']} ответил AI" + (f", {len(manual)} ждут тебя" if manual else ""))
    if c["form_sent"]:
        lines.append(f"Анкеты: {c['form_sent']} отправлено")
    for r in manual[:5]:
        lines.append(f"🙋 {r.get('employer') or '?'} · {r.get('vacancy') or ''}\n{r.get('url') or ''}")
    if c["error"] >= 5:
        lines.append("⚠️ Много ошибок: возможно, hh ограничивает IP или пропадала связь")
    return "\n".join(lines)


class Operation(BaseOperation):
    """Отправить в Telegram сводку за день по журналу AI: отклики, чаты, кто ждёт ответа"""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-n", "--dry-run", action="store_true", help="Показать сводку, не отправляя")

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        path = tool.config_dir_path / JOURNAL_FILENAME
        records = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        text = summarize(records, date.today().isoformat())
        if not text:
            print("За сегодня в журнале пусто, сводку не шлём")
            return
        print(text)
        if args.dry_run:
            return
        if notify.send(tool.config, text):
            print("📨 Сводка отправлена в Telegram")
        elif not all(notify.settings(tool.config)):
            print("Telegram не настроен: задайте bot_token и chat_id в разделе «Система»")
