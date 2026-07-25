from __future__ import annotations

import html
import random
import re
from typing import Any


def find_page_marker(text: str, marker: str) -> tuple[str, int]:
    """Ищет маркер во встроенном в страницу JSON.

    hh.ru отдаёт состояние страницы внутри HTML-атрибута и экранирует кавычки
    (`&#34;vacancyTests&#34;:` вместо `"vacancyTests":`), поэтому поиск по
    сырому тексту промахивается. Если маркер не нашёлся — распаковываем
    HTML-сущности и ищем снова. Возвращает текст, в котором искали, и позицию
    маркера (-1, если его нет и там).
    """
    pos = text.find(marker)
    if pos == -1:
        text = html.unescape(text)
        pos = text.find(marker)
    return text, pos


def shorten(s: str, limit: int = 75, ellipsis: str = "…") -> str:
    return s[:limit] + bool(s[limit:]) * ellipsis


def rand_text(s: str) -> str:
    while (
        temp := re.sub(
            r"{([^{}]+)}",
            lambda m: random.choice(
                m.group(1).split("|"),
            ),
            s,
        )
    ) != s:
        s = temp
    return s


def bool2str(v: bool) -> str:
    return str(v).lower()


# К удалению
def list2str(items: list[Any] | None) -> str:
    return ",".join(f"{v}" for v in items) if items else ""


def unescape_string(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\t", "\t")
        .replace(r"\\", "\\")
    )


def br2nl(s: str) -> str:
    return re.sub(r"<br\s*/?>", "\n", s, flags=re.I)


def strip_tags(content: str) -> str:
    content = br2nl(content)
    content = re.sub(r"<[^>]+>", "", content)
    # content = re.sub(r"\s+", " ", content)
    return content.strip()
