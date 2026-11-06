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
        from pathlib import Path

        from ..constants import CONFIG_DIR

        # Флаг --config-dir утилита применяет поздно, поэтому берём путь
        # так же, как она: аргумент, затем CONFIG_DIR, затем путь по умолчанию
        base = Path(
            getattr(args, "config_dir", None) or os.getenv("CONFIG_DIR") or CONFIG_DIR
        ).expanduser().resolve()
        profile = getattr(args, "profile_id", None) or os.getenv("HH_PROFILE_ID") or "."
        os.environ.pop("HH_PROFILE_ID", None)
        serve(base, profile, args.host, args.port, args.token)
