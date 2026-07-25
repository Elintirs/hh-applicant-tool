# Этот модуль можно использовать как образец для других
from __future__ import annotations

import argparse
import html
import logging
import re
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    pass


class Operation(BaseOperation):
    """Проверка браузерной сессии, полученной при авторизации"""

    __aliases__: list[str] = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        # parser
        ...

    def run(self, tool: HHApplicantTool, args: BaseNamespace) -> None:
        r = tool.session.get("https://hh.ru")
        # hh экранирует встроенный JSON (&#34; вместо "), поэтому ищем
        # состояние сессии по распакованному тексту страницы.
        text = html.unescape(r.text)

        if m := re.search(r'^\s+login: "([^"]+)', text, re.MULTILINE):
            print("Вы вошли как", m.group(1))
        elif m := re.search(r'"userType":"(\w+)"', text):
            user_type = m.group(1)
            if user_type == "anonymous":
                logger.warning("Вы не авторизованы!")
            else:
                print(f"Вы авторизованы (userType: {user_type})")
        else:
            logger.warning(
                "Не удалось определить состояние сессии — разметка hh.ru изменилась?"
            )
