"""Кто пишет в чате со стороны работодателя: человек, бот или непонятно.

Флаг hh isBot ненадёжен: «Робот-рекрутер» обычно помечен человеком, а ИИ-ассистенты
пишут под обычными именами. Поэтому решение по нескольким признакам. Смотрим на
автора последнего сообщения работодателя: человек мог подключиться после бота.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

HUMAN, BOT, UNCLEAR = "human", "bot", "unclear"

BOT_NAME_RE = re.compile(
    r"бот\b|робот|\bии\b|ии-|\bai\b|помощник|ассистент|assistant|\bbot\b|"
    r"technical interviews|certif",
    re.I,
)
# Самопредставление автоматики в тексте
BOT_SELF_RE = re.compile(
    r"я\s+(?:\S+\s+){0,2}(?:чат-?бот|бот|робот|ии|ai|виртуальн\w+ помощник|ассистент\w*)\b|"
    r"на базе (?:ai|ии|искусственного интеллекта)|диалог записывается|"
    r"я\s+(?:hr|ai|ии)[- ]?ассистент|искусственный интеллект|ии-ассистент|ai-ассистент",
    re.I,
)
# Автоответ «резюме получили, рассмотрим»: приходит под именем рекрутера, но писал не он
ACK_RE = re.compile(
    r"рассмотр\w* (?:ваше )?резюме|(?:взяли|передали) (?:ваше )?резюме|резюме получено|"
    r"мы (?:с вами )?свяжемся|сообщит вам о|верн[её]мся с обратной связью|изучим (?:ваше )?резюме",
    re.I,
)
# Системные сообщения hh: не автор переписки
SYSTEM_AUTHORS = {"hh.ru", "headhunter"}
FAST_REPLY_SEC = 60
TEMPLATE_MIN_CHATS = 3


def template_key(text: str) -> str:
    """Нормализованное начало сообщения: по нему ищем одинаковые рассылки."""
    t = re.sub(r"https?://\S+", "", (text or "").lower())
    t = re.sub(r"[^\wа-яё]+", " ", t)
    # Обращение по имени в начале не делает текст личным
    t = re.sub(r"^(?:\S+ ){0,3}?(здравствуйте|добрый день|привет)\b", r"\1", t.strip())
    return t[:120]


def _ts(m: dict[str, Any]) -> float | None:
    at = m.get("at")
    if not at:
        return None
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(
    messages: Iterable[dict[str, Any]],
    template_counts: dict[str, int] | None = None,
) -> tuple[str, list[str]]:
    """messages: {text, mine, author, is_bot, at}. template_counts: сколько разных
    чатов получили сообщение с таким template_key. Возвращает (вид, причины)."""
    msgs = [m for m in messages if (m.get("author") or "").strip().lower() not in SYSTEM_AUTHORS]
    theirs = [m for m in msgs if not m.get("mine")]
    if not theirs:
        return UNCLEAR, ["работодатель ещё не писал"]
    author = (theirs[-1].get("author") or "").strip()
    by_author = [m for m in theirs if (m.get("author") or "").strip() == author]

    reasons: list[str] = []
    if any(m.get("is_bot") for m in by_author):
        reasons.append("hh помечает как бота")
    if author and BOT_NAME_RE.search(author):
        reasons.append(f"имя «{author}»")
    if any(BOT_SELF_RE.search(m.get("text") or "") for m in by_author):
        reasons.append("представляется ботом")
    # Отвечает быстрее минуты после моих сообщений, и не раз
    fast, prev_mine = 0, None
    for m in msgs:
        if m.get("mine"):
            prev_mine = _ts(m)
        elif (m.get("author") or "").strip() == author and prev_mine is not None:
            t = _ts(m)
            if t is not None and 0 <= t - prev_mine < FAST_REPLY_SEC:
                fast += 1
            prev_mine = None
    if fast >= 2:
        reasons.append(f"отвечает за секунды ({fast} раза)")
    if reasons:
        return BOT, reasons

    texts_all = [m.get("text") or "" for m in by_author if (m.get("text") or "").strip()]
    if texts_all and all(ACK_RE.search(t) and "?" not in t for t in texts_all):
        return UNCLEAR, ["только автоответ «рассмотрим резюме»"]

    counts = template_counts or {}
    texts = [m.get("text") or "" for m in by_author if (m.get("text") or "").strip()]
    personal = [t for t in texts if counts.get(template_key(t), 0) < TEMPLATE_MIN_CHATS]
    if texts and not personal:
        n = max(counts.get(template_key(t), 0) for t in texts)
        return UNCLEAR, [f"шаблон, такой же текст в {n} чатах"]
    if not author:
        return UNCLEAR, ["автор не указан"]
    return HUMAN, [f"пишет «{author}»"]


def score(kind: str, state: str, last_is_mine: bool) -> int:
    """Важность чата для сортировки: приглашения от людей выше всего."""
    invite = state in ("INVITATION", "INTERVIEW", "OFFER", "HIRED")
    base = {HUMAN: 30, UNCLEAR: 10, BOT: 0}.get(kind, 10)
    return base + (20 if invite else 0) + (0 if last_is_mine else 5)
