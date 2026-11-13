# Этот модуль можно использовать как образец для других
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..api import ApiError, datatypes
from ..tool import BaseNamespace, BaseOperation
from ..utils.date import parse_api_datetime
from ..utils.string import shorten

if TYPE_CHECKING:
    from ..tool import HHApplicantTool

logger = logging.getLogger(__name__.split(".")[0])


class Namespace(BaseNamespace):
    pass


def _why_not(resume: datatypes.Resume) -> str:
    """Почему hh не даёт поднять резюме, человеческими словами."""
    if resume.get("blocked"):
        return "резюме заблокировано модератором"
    status = (resume.get("status") or {}).get("id")
    if status and status != "published":
        return "резюме не опубликовано, сначала опубликуйте его на hh.ru"
    next_at = resume.get("next_publish_at")
    if next_at:
        try:
            when = parse_api_datetime(next_at).astimezone()
        except ValueError:
            return (
                f"hh разрешает поднимать раз в 4 часа, следующий раз {next_at}"
            )
        left = max(
            0, int((when - datetime.now(timezone.utc)).total_seconds() // 60)
        )
        hours, minutes = divmod(left, 60)
        wait = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
        return (
            "hh разрешает поднимать раз в 4 часа, "
            f"следующий раз в {when:%H:%M} (через {wait})"
        )
    return "hh пока не разрешает его поднять"


class Operation(BaseOperation):
    """Обновить все резюме"""

    __aliases__: list[str] = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, tool: HHApplicantTool, args: BaseNamespace) -> None:
        resumes: list[datatypes.Resume] = tool.get_resumes()
        for resume in resumes:
            if not resume.get("can_publish_or_update"):
                logger.warning(
                    f"Не могу обновить «{shorten(resume.get('title') or '')}»: "
                    f"{_why_not(resume)}. {resume['alternate_url']}"
                )
                continue
            try:
                r = tool.api_client.post(
                    f"/resumes/{resume['id']}/publish",
                )
                assert {} == r
                print(
                    "✅ Обновлено",
                    resume["alternate_url"],
                    "-",
                    shorten(resume["title"]),
                )
            except ApiError as ex:
                logger.error(f"Ошибка при обновлении резюме: {ex}")
