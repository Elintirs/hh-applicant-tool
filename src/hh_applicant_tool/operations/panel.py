from __future__ import annotations

import argparse
import logging
import os
from typing import TYPE_CHECKING

from ..tool import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..tool import HHApplicantTool


logger = logging.getLogger(__name__.split(".")[0])


class Namespace(BaseNamespace):
    host: str
    port: int
    token: str | None


class Operation(BaseOperation):
    """Веб-панель: запуск команд с живым логом, расписание, модели по
    задачам, шаблоны писем, статистика откликов и редактор конфига.

    Панель запускает любые команды и показывает ключи API, поэтому по
    умолчанию слушает только 127.0.0.1. В Docker укажите --host 0.0.0.0 и
    пробросьте порт на 127.0.0.1 хоста. Для доступа по сети задайте токен."""

    __aliases__: list[str] = ["web", "webpanel"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host",
            default=os.getenv("HH_PANEL_HOST", "127.0.0.1"),
            help="Адрес, на котором слушает панель",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=int(os.getenv("HH_PANEL_PORT", "8090")),
            help="Порт панели",
        )
        parser.add_argument(
            "--token",
            default=os.getenv("HH_PANEL_TOKEN"),
            help="Токен доступа. Без него панель открывается без пароля. Можно задать через HH_PANEL_TOKEN",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        from ..panel.server import serve

        if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
            logger.warning(
                "Панель слушает %s без токена. Не открывайте этот порт в сеть!",
                args.host,
            )
        # Профиль уже учтён в config_dir_path, дочерним процессам его не
        # передаём, чтобы путь не удвоился
        os.environ.pop("HH_PROFILE_ID", None)
        serve(tool.config_dir_path, args.host, args.port, args.token)
