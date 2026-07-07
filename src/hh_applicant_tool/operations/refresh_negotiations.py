from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    status: str


class Operation(BaseOperation):
    """Синхронизировать отклики (негоциации) с hh.ru в локальную БД.

    Нужно для статистики и поиска по откликам. Тянет список негоциаций
    постранично и сохраняет в локальную базу (upsert).
    """

    __aliases__ = ["sync-negotiations", "refresh-neg"]

    def setup_parser(self, parser):
        parser.add_argument(
            "--status",
            default="active",
            help="Статус негоциаций для выборки (по умолчанию: active)",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        count = 0
        for item in tool.get_negotiations(args.status):
            # save() принимает сырой словарь и сам делает from_api
            tool.storage.negotiations.save(item)
            count += 1
            if count % 100 == 0:
                print(f"… загружено {count}")
        print(f"✅ Синхронизировано откликов: {count}")
