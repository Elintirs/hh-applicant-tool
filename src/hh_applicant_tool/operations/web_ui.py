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
    """Запуск веб-интерфейса (работает в браузере, в т.ч. в Docker).

    В отличие от команды `ui`, которая открывает нативное окно через
    pywebview, эта команда отдаёт тот же интерфейс по HTTP. Подходит для
    серверов и контейнеров без графической оболочки. После запуска откройте
    в браузере http://<host>:<port> (по умолчанию http://localhost:8080).

    В Docker запускайте с `--host 0.0.0.0` и пробросьте порт наружу.
    """

    def setup_parser(self, parser):
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Адрес прослушивания. В Docker укажите 0.0.0.0",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8080,
            help="Порт (по умолчанию 8080)",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None | int:
        from ..ui.web import serve

        serve(tool, host=args.host, port=args.port)
