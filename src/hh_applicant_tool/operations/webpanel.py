from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    host: str
    port: int


class Operation(BaseOperation):
    """Веб-панель управления утилитой.

    Поднимает локальный веб-сервер, который через интроспекцию CLI показывает
    ВСЕ команды и ВСЕ их параметры в виде форм, запускает их с живым выводом и
    вводом (для интерактивных вроде auth), и даёт полный редактор config.json.

    Открывается в браузере. В Docker: --host 0.0.0.0 и проброс порта.
    Только для localhost — панель запускает произвольные команды и показывает токены.
    """

    __aliases__ = ["panel", "web-panel"]

    def setup_parser(self, parser):
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Адрес прослушивания. В Docker укажите 0.0.0.0",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8090,
            help="Порт (по умолчанию 8090)",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None | int:
        from ..webpanel.server import serve

        serve(tool, host=args.host, port=args.port)
