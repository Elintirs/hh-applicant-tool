"""Google Forms: разбор анкеты по ссылке и заполнение ответами модели.

Работодатели часто присылают в чат ссылку на Google Forms. Опубликованная
форма хранит своё описание прямо в HTML в переменной FB_PUBLIC_LOAD_DATA_:
вопросы, их тип, варианты, обязательность и entry-идентификаторы полей.
Ответы отправляются обычным POST на тот же адрес с /formResponse.

Отправка необратима, поэтому модуль только готовит черновик: заполнение
и submit вызываются отдельно, по явному действию пользователя.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from .operations.apply_vacancies import _TEST_RESUME_BLOCK, ai_call
from .utils.string import choose_similar, render_template

# Ссылку на форму ищем и в коротком виде forms.gle, и в полном docs.google.com
FORM_URL_RE = re.compile(
    r"https?://(?:forms\.gle/\S+|docs\.google\.com/forms/\S+)", re.I
)
_FB_RE = re.compile(r"FB_PUBLIC_LOAD_DATA_ ?= ?(\[.*?\]) ?;</script>", re.S)
_FBZX_RE = re.compile(r'name="fbzx"\s+value="(-?\d+)"')

# Коды типов вопросов в FB_PUBLIC_LOAD_DATA_
SHORT, PARAGRAPH, RADIO, DROPDOWN, CHECKBOX = 0, 1, 2, 3, 4
TEXT_TYPES = {SHORT, PARAGRAPH}
CHOICE_TYPES = {RADIO, DROPDOWN, CHECKBOX}
# Типы, которые сами по себе не вопросы или мы не заполняем
SKIP_TYPES = {6, 8, 11}  # заголовок секции, разрыв страницы, картинка

_CHOICE_PROMPT = _TEST_RESUME_BLOCK + """
Вопрос анкеты: %(question)s
Варианты:
%(reply_options)s

Выбери подходящий вариант. Ответь только его текстом, без пояснений."""

_TEXT_PROMPT = _TEST_RESUME_BLOCK + """
Вопрос анкеты: %(question)s"""


@dataclass
class Question:
    entry_id: int
    title: str
    type: int
    options: list[str] = field(default_factory=list)
    required: bool = False
    multiple: bool = False  # для чекбоксов


@dataclass
class Form:
    response_url: str  # куда слать ответ (…/formResponse)
    title: str
    questions: list[Question]
    fbzx: str | None = None
    needs_login: bool = False  # форма собирает почту / требует вход в Google


class FormError(RuntimeError):
    pass


def find_form_url(text: str) -> str | None:
    m = FORM_URL_RE.search(text or "")
    return m.group(0).rstrip(').,;»"\'') if m else None


def _parse_item(item: list) -> Question | None:
    typ = item[3]
    if typ in SKIP_TYPES:
        return None
    subs = item[4] or []
    if not subs or not subs[0]:
        return None
    sub = subs[0]
    entry_id = sub[0]
    if entry_id is None:
        return None
    raw_opts = sub[1] if len(sub) > 1 else None
    options = [str(o[0]) for o in (raw_opts or []) if o and o[0] not in (None, "")]
    required = bool(sub[2]) if len(sub) > 2 else False
    return Question(
        entry_id=int(entry_id),
        title=str(item[1] or "").strip(),
        type=int(typ),
        options=options,
        required=required,
        multiple=typ == CHECKBOX,
    )


def parse_form(html: str, final_url: str) -> Form:
    """Разбирает HTML опубликованной формы. final_url — адрес после редиректов."""
    if "accounts.google.com" in final_url:
        raise FormError("Форма требует входа в Google")
    m = _FB_RE.search(html)
    if not m:
        raise FormError("Это не похоже на открытую Google-форму")
    data = json.loads(m.group(1))
    items = (data[1] or [None, None])[1] or []
    questions = [q for it in items if (q := _parse_item(it))]
    if not questions:
        raise FormError("В форме не нашлось вопросов для заполнения")
    # data[1][10][6] = 1 обычно означает сбор почты / вход
    settings = (data[1][10] if len(data[1]) > 10 else None) or []
    needs_login = bool(settings[6]) if len(settings) > 6 else False
    fbzx = (mm := _FBZX_RE.search(html)) and mm.group(1) or None
    response_url = re.sub(r"/viewform.*$", "/formResponse", final_url.split("?")[0])
    return Form(
        response_url=response_url,
        title=str((data[1] or [""])[8] or data[3] or "Анкета"),
        questions=questions,
        fbzx=fbzx,
        needs_login=needs_login,
    )


def fetch_form(url: str, session: requests.Session | None = None) -> Form:
    s = session or requests.Session()
    r = s.get(url, allow_redirects=True, timeout=30,
              headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return parse_form(r.text, r.url)


def answer_form(
    form: Form,
    placeholders: dict[str, Any],
    chat: Any,
) -> list[tuple[Question, Any]]:
    """Заполняет форму ответами модели. chat — AIChat (как у тестов).
    Возвращает пары (вопрос, ответ): строка, либо список для чекбоксов."""
    out: list[tuple[Question, Any]] = []
    for q in form.questions:
        if q.type in CHOICE_TYPES and q.options:
            prompt = render_template(
                _CHOICE_PROMPT,
                {**placeholders, "question": q.title,
                 "reply_options": "\n".join(q.options)},
                "промпта варианта анкеты",
            )
            ans, _ = ai_call(chat, prompt, "анкета")
            if q.multiple:
                picked = [o for o in q.options if o and o in ans]
                value: Any = picked or [choose_similar(ans, q.options)]
            else:
                value = choose_similar(ans.strip(), q.options)
        else:
            prompt = render_template(
                _TEXT_PROMPT, {**placeholders, "question": q.title},
                "промпта ответа анкеты",
            )
            ans, _ = ai_call(chat, prompt, "анкета")
            value = ans.strip()
        out.append((q, value))
    return out


def build_payload(answers: list[tuple[Question, Any]]) -> dict[str, Any]:
    """Тело POST на /formResponse. Список значений (чекбокс) requests
    отправит повторяющимися полями, как и ждёт Google."""
    payload: dict[str, Any] = {"fvv": "1", "pageHistory": "0", "submit": "Submit"}
    for q, value in answers:
        if value in (None, "", []):
            continue
        payload[f"entry.{q.entry_id}"] = value
    return payload


def submit_form(
    form: Form,
    answers: list[tuple[Question, Any]],
    session: requests.Session | None = None,
) -> bool:
    """Отправляет заполненную форму. True — Google принял ответ."""
    s = session or requests.Session()
    payload = build_payload(answers)
    if form.fbzx:
        payload["fbzx"] = form.fbzx
    r = s.post(form.response_url, data=payload, allow_redirects=False, timeout=30,
               headers={"User-Agent": "Mozilla/5.0"})
    return r.status_code in (200, 302)
