"""Схема команд CLI для форм панели.

Строится из настоящего argparse-парсера утилиты, поэтому новые флаги
апстрима появляются в панели сами. Вручную задаём только, какие флаги
показывать сверху формы, и человеческие названия команд.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .hh import PanelTool

# Команды, которые в панели не нужны или запускаются иначе
HIDDEN_OPERATIONS = {"panel", "log", "config"}

# Глобальные флаги, которые панель ставит сама или которые не имеют смысла
# без терминала
HIDDEN_GLOBAL = {
    "help",
    "config_dir",
    "profile_id",
    "use_kitty",
    "use_sixel",
}

OPERATION_TITLES = {
    "apply-vacancies": "Откликнуться на вакансии",
    "reply-chats": "Автоответчик в чатах",
    "update-resumes": "Поднять резюме",
    "authorize": "Вход в hh.ru",
    "whoami": "Кто я",
    "list-resumes": "Список резюме",
    "clear-negotiations": "Почистить отклики",
    "call-api": "Вызов API",
    "check-updates": "Проверить обновления",
    "logout": "Выйти из аккаунта",
    "log": "Лог утилиты",
    "config": "Конфиг",
}

OPERATION_ORDER = [
    "apply-vacancies",
    "reply-chats",
    "update-resumes",
    "clear-negotiations",
    "authorize",
    "whoami",
    "list-resumes",
    "check-updates",
    "call-api",
    "logout",
]

# Флаги, которые показываются сразу. Остальные уходят в «Дополнительно»
PRIMARY_FLAGS = {
    "apply-vacancies": [
        "text",
        "resume_id",
        "dry_run",
        "max_responses",
        "total_pages",
        "ai_filter",
        "excluded_filter",
        "force_cover_letter",
        "letter_template",
    ],
    "reply-chats": ["resume_id", "only_bots", "loop", "max_pages", "dry_run"],
    "clear-negotiations": ["older_than", "blacklist_discard", "dry_run"],
}

# Команды, которые меняют что-то на hh.ru: в форме для них выделяем dry-run
MUTATING = {"apply-vacancies", "reply-chats", "clear-negotiations", "logout"}


def _type_name(action: argparse.Action) -> str:
    t = action.type
    if t is int:
        return "int"
    if t is float:
        return "float"
    if t is Path:
        return "path"
    return "str"


def _kind(action: argparse.Action) -> str:
    if isinstance(action, argparse.BooleanOptionalAction):
        return "bool"
    if isinstance(action, argparse._StoreTrueAction):
        return "flag"
    if isinstance(action, argparse._StoreFalseAction):
        return "flag_false"
    if isinstance(action, argparse._CountAction):
        return "count"
    if isinstance(action, argparse._AppendAction):
        return "append"
    if not action.option_strings:
        return "positional"
    return "value"


def _default(action: argparse.Action) -> Any:
    d = action.default
    if d is argparse.SUPPRESS or d is None:
        return None
    if isinstance(d, (str, int, float, bool)):
        return d
    if isinstance(d, (list, tuple)):
        return [str(x) for x in d]
    return str(d)


def _describe(action: argparse.Action) -> dict[str, Any]:
    flag = None
    if action.option_strings:
        # Самый длинный вариант обычно самый понятный: --dry-run, а не -n
        longs = [o for o in action.option_strings if o.startswith("--")]
        flag = longs[0] if longs else action.option_strings[0]
    neg_flag = None
    if isinstance(action, argparse.BooleanOptionalAction) and flag:
        neg_flag = "--no-" + flag[2:]
    nargs = action.nargs
    multiple = nargs in ("+", "*") or (isinstance(nargs, int) and nargs > 1)
    return {
        "dest": action.dest,
        "flag": flag,
        "neg_flag": neg_flag,
        "aliases": list(action.option_strings),
        "kind": _kind(action),
        "type": _type_name(action),
        "choices": [str(c) for c in action.choices] if action.choices else None,
        "multiple": multiple,
        "required": bool(getattr(action, "required", False)),
        "default": _default(action),
        "help": (action.help or "").replace("%(default)s", str(_default(action))),
        "metavar": action.metavar if isinstance(action.metavar, str) else None,
    }


def _actions_by_group(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    groups = []
    seen: set[int] = set()
    for group in parser._action_groups:
        items = []
        for action in group._group_actions:
            if id(action) in seen:
                continue
            seen.add(id(action))
            if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                continue
            items.append(_describe(action))
        if items:
            title = group.title or ""
            if title in ("options", "optional arguments"):
                title = ""
            elif title == "positional arguments":
                title = ""
            groups.append({"title": title, "args": items})
    # Взаимоисключающие группы: отметим, чтобы форма не дала выбрать два
    for mx in parser._mutually_exclusive_groups:
        dests = [a.dest for a in mx._group_actions]
        for g in groups:
            for a in g["args"]:
                if a["dest"] in dests:
                    a["exclusive_with"] = [d for d in dests if d != a["dest"]]
    return groups


def operation_title(args: list[str]) -> str:
    """Человеческое название задачи по аргументам команды."""
    parser = PanelTool.parser()
    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    for arg in args:
        if arg.startswith("-"):
            continue
        op_parser = sub.choices.get(arg)
        if op_parser is None:
            continue
        for name, p in sub.choices.items():
            if p is op_parser and name in OPERATION_TITLES:
                return OPERATION_TITLES[name]
        return arg
    return args[0] if args else "Команда"


def build_schema() -> dict[str, Any]:
    parser = PanelTool.parser()

    global_args = [
        _describe(a)
        for a in parser._actions
        if a.dest not in HIDDEN_GLOBAL
        and not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))
    ]

    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    # В choices лежат и имена, и алиасы, указывающие на один парсер
    by_parser: dict[int, dict[str, Any]] = {}
    for name, op_parser in sub.choices.items():
        entry = by_parser.get(id(op_parser))
        if entry is None:
            by_parser[id(op_parser)] = {
                "name": name,
                "aliases": [],
                "parser": op_parser,
            }
        else:
            entry["aliases"].append(name)

    operations = []
    for entry in by_parser.values():
        name = entry["name"]
        if name in HIDDEN_OPERATIONS:
            continue
        op_parser: argparse.ArgumentParser = entry["parser"]
        operations.append(
            {
                "name": name,
                "title": OPERATION_TITLES.get(name, name),
                "aliases": entry["aliases"],
                "help": (op_parser.description or "").strip(),
                "groups": _actions_by_group(op_parser),
                "primary": PRIMARY_FLAGS.get(name),
                "mutating": name in MUTATING,
            }
        )

    def order(op: dict[str, Any]) -> tuple[int, str]:
        try:
            return (OPERATION_ORDER.index(op["name"]), op["name"])
        except ValueError:
            return (len(OPERATION_ORDER), op["name"])

    operations.sort(key=order)
    return {"global": global_args, "operations": operations}
