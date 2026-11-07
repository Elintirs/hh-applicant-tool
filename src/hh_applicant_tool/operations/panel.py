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
    tls: bool | None
    public_host: list[str]


class Operation(BaseOperation):
    """Веб-панель: запуск команд с живым логом, расписание, модели по
    задачам, шаблоны писем, статистика откликов и редактор конфига.

    Панель запускает любые команды и показывает ключи API, поэтому по
    умолчанию слушает только 127.0.0.1. В Docker укажите --host 0.0.0.0 и
    пробросьте порт. Если панель слушает не только localhost, она сама
    включает токен и HTTPS, чтобы её можно было открывать с ноутбука."""

    __aliases__: list[str] = ["web", "webpanel"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host",
            default=os.getenv("HH_PANEL_HOST", "127.0.0.1"),
            help="Адрес, на котором слушает панель. 0.0.0.0, чтобы открывать с других устройств в сети",
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
            help="Токен доступа. Если панель слушает не только localhost и токен не задан, он создаётся сам и сохраняется в panel-token.txt",
        )
        parser.add_argument(
            "--tls",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="HTTPS с самоподписанным сертификатом. По умолчанию включён, если панель открыта в сеть. Можно задать HH_PANEL_TLS=0/1",
        )
        parser.add_argument(
            "--public-host",
            action="append",
            default=[h for h in os.getenv("HH_PANEL_PUBLIC_HOSTS", "").replace(" ", "").split(",") if h],
            help="Адрес, по которому панель открывают с других устройств (IP ПК в сети). Попадёт в сертификат. Можно через запятую в HH_PANEL_PUBLIC_HOSTS",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        from pathlib import Path

        from ..constants import CONFIG_DIR
        from ..panel import remote
        from ..panel.server import serve

        # Флаг --config-dir утилита применяет поздно, поэтому берём путь
        # так же, как она: аргумент, затем CONFIG_DIR, затем путь по умолчанию
        base = Path(
            getattr(args, "config_dir", None) or os.getenv("CONFIG_DIR") or CONFIG_DIR
        ).expanduser().resolve()
        profile = getattr(args, "profile_id", None) or os.getenv("HH_PROFILE_ID") or "."
        os.environ.pop("HH_PROFILE_ID", None)

        exposed = not remote.is_local(args.host)
        token = args.token
        if exposed:
            token, created = remote.ensure_token(base, token)
            if created:
                print(f"Создан токен доступа к панели: {token}", flush=True)
                print(f"Он сохранён в {base / remote.TOKEN_FILE}", flush=True)
        tls = args.tls
        if tls is None:
            env = os.getenv("HH_PANEL_TLS")
            tls = exposed if env is None else env.lower() in ("1", "true", "yes", "on")
        if exposed and not tls:
            logger.warning("Панель открыта в сеть без HTTPS: токен уйдёт по сети открытым текстом")
        public_port = int(os.getenv("HH_PANEL_PUBLIC_PORT", "0")) or None
        serve(base, profile, args.host, args.port, token, tls=tls, public_hosts=args.public_host, public_port=public_port)
