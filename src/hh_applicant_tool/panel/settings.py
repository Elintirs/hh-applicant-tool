"""Конфиг утилиты: сырой TOML, модели по задачам, шаблоны писем.

Читаем и пишем через ConfigTOML апстрима. Любая запись делает бэкап
config.toml.bak: tomli_w не умеет сохранять комментарии, и пользователь
должен иметь возможность откатиться.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

import requests

from .. import ai
from ..constants import CONFIG_FILENAME
from ..utils.config import ConfigTOML
from ..utils.helpers import get_proxies_dict
from ..utils.string import rand_text, render_template

# Задачи, для которых утилита берёт свою секцию [openai.<key>]. Всё, что не
# задано в секции, наследуется из [openai.default]
AI_TASKS: list[dict[str, Any]] = [
    {
        "key": "default",
        "title": "Основная модель",
        "hint": "Используется везде, где для задачи не задана своя модель.",
    },
    {
        "key": "cover_letter",
        "title": "Сопроводительные письма",
        "hint": "Пишет письмо к отклику по промпту из раздела «Письма».",
    },
    {
        "key": "vacancy_filter",
        "title": "Фильтр вакансий",
        "hint": "Решает, подходит ли вакансия резюме. Включается флагом --ai-filter.",
    },
    {
        "key": "test_solver",
        "title": "Тесты работодателей",
        "hint": "Отвечает на вопросы теста при отклике.",
    },
    {
        "key": "reply_chat",
        "title": "Ответы в чатах",
        "hint": "Пишет ответы работодателям в автоответчике.",
    },
    {
        "key": "reply_chat_options",
        "title": "Выбор вариантов в чатах",
        "hint": "Выбирает вариант, когда работодатель предлагает кнопки.",
    },
    {
        "key": "captcha",
        "title": "Распознавание капчи",
        "hint": "Нужна модель с поддержкой изображений (vision). В Docker другого способа решить капчу нет.",
        "vision": True,
    },
]
AI_TASK_KEYS = [t["key"] for t in AI_TASKS]

# Поля секции, которые показываем в форме. Остальные ключи секции не трогаем
AI_FIELDS = [
    "base_url",
    "model",
    "api_key",
    "temperature",
    "max_completion_tokens",
    "timeout",
    "connect_timeout",
    "delay",
    "proxy_url",
]
AI_NUMBER_FIELDS = {"temperature", "max_completion_tokens", "timeout", "connect_timeout", "delay"}
AI_INT_FIELDS = {"max_completion_tokens"}

LETTER_KEYS = ("cover_letter", "cover_letter_prompt", "cover_letter_templates")

SAMPLE_PLACEHOLDERS = {
    "first_name": "Иван",
    "last_name": "Петров",
    "email": "ivan@example.com",
    "phone": "+7 900 000-00-00",
    "resume_hash": "0123456789abcdef",
    "resume_title": "Python-разработчик",
    "resume_url": "https://hh.ru/resume/0123456789abcdef",
    "resume_city": "Москва",
    "resume_about": "Пишу бэкенд на Python 6 лет.",
    "resume_skills": "Python, Django, PostgreSQL",
    "resume_salary": "250000 RUR",
    "resume_work_format": "Удалённо",
    "resume_employment_form": "Полная",
    "resume_total_experience": "6 г. 2 мес.",
    "github_profile_url": "https://github.com/example",
    "vacancy_name": "Senior Python Developer",
    "vacancy_url": "https://hh.ru/vacancy/1",
    "employer_name": "ООО Ромашка",
}


class SettingsError(ValueError):
    pass


class _Loose(dict):
    """Для предпросмотра промптов: неизвестный плейсхолдер видно как ‹имя›."""

    def __missing__(self, key: str) -> str:
        return f"‹{key}›"


def _text_keys() -> list[dict[str, Any]]:
    from ..operations import apply_vacancies as av
    from ..operations import reply_chats as rc

    return [
        {"key": "ai_filter_prompt", "group": "vacancies", "title": "Фильтр вакансий", "hint": "Модель отвечает да или нет. Используется с флагом --ai-filter.", "default": av.DEFAULT_AI_FILTER_PROMPT},
        {"key": "test_choice_prompt", "group": "tests", "title": "Тест: выбор варианта", "hint": "Вопрос теста работодателя с вариантами ответа.", "default": av.DEFAULT_TEST_CHOICE_PROMPT},
        {"key": "test_text_prompt", "group": "tests", "title": "Тест: свободный ответ", "hint": "Вопрос теста, где нужно написать ответ.", "default": av.DEFAULT_TEST_TEXT_PROMPT},
        {"key": "reply_chat_user_prompt", "group": "chats", "title": "Ответ работодателю", "hint": "Когда работодатель написал в чат.", "default": rc.DEFAULT_USER_PROMPT},
        {"key": "reply_chat_options_prompt", "group": "chats", "title": "Выбор кнопки", "hint": "Когда в сообщении есть варианты ответа.", "default": rc.DEFAULT_OPTIONS_PROMPT},
        {"key": "reply_chat_bump_prompt", "group": "chats", "title": "Напоминание о себе", "hint": f"Если работодатель молчит дольше {rc.BUMP_AFTER_DAYS} дней.", "default": rc.DEFAULT_BUMP_PROMPT},
        {"key": "reply_chat_farewell_message", "group": "chats", "title": "Прощание при отказе", "hint": "После него автоответчик выходит из чата.", "default": rc.FAREWELL_MESSAGE},
        {"key": "apply_mail_subject", "group": "mail", "title": "Тема письма на почту", "hint": "Для отклика с флагом --send-email.", "default": av.DEFAULT_APPLY_MAIL_SUBJECT},
        {"key": "apply_mail_body", "group": "mail", "title": "Текст письма на почту", "hint": "HTML разрешён.", "default": av.DEFAULT_APPLY_MAIL_BODY},
    ]


class Settings:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.path = config_dir / CONFIG_FILENAME

    # ---- общее -----------------------------------------------------------

    def _config(self) -> ConfigTOML:
        # Каждый раз читаем заново: файл мог поменять CLI или человек руками
        return ConfigTOML(self.path)

    def _backup(self) -> None:
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(".toml.bak"))

    def _save(self, cfg: ConfigTOML) -> None:
        self._backup()
        cfg.save()

    def has_comments(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        return any(line.lstrip().startswith("#") for line in text.splitlines())

    # ---- сырой TOML ----------------------------------------------------

    def read_raw(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        backup = self.path.with_suffix(".toml.bak")
        return {
            "path": str(self.path),
            "text": text,
            "has_backup": backup.exists(),
        }

    @staticmethod
    def validate_raw(text: str) -> None:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as ex:
            raise SettingsError(f"Ошибка в TOML: {ex}") from ex

    def write_raw(self, text: str) -> None:
        self.validate_raw(text)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._backup()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def restore_backup(self) -> None:
        backup = self.path.with_suffix(".toml.bak")
        if not backup.exists():
            raise SettingsError("Резервной копии нет")
        current = self.path.read_bytes() if self.path.exists() else None
        shutil.copy2(backup, self.path)
        # Текущую версию кладём на место бэкапа, чтобы откат был обратимым
        if current is not None:
            backup.write_bytes(current)

    # ---- модели --------------------------------------------------------

    def read_models(self) -> dict[str, Any]:
        cfg = self._config()
        default = cfg.get_section("openai.default")
        tasks = []
        for task in AI_TASKS:
            own = cfg.get_section(f"openai.{task['key']}")
            effective = {**default, **own} if task["key"] != "default" else own
            tasks.append(
                {
                    **task,
                    "values": {k: own.get(k) for k in AI_FIELDS if k in own},
                    "effective": {
                        k: effective.get(k) for k in AI_FIELDS if k in effective
                    },
                    "extra_keys": sorted(set(own) - set(AI_FIELDS)),
                }
            )
        return {
            "tasks": tasks,
            "configured": bool(cfg.get("openai")),
            "has_comments": self.has_comments(),
        }

    @staticmethod
    def _clean_ai_values(values: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in AI_FIELDS:
            v = values.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            if key in AI_NUMBER_FIELDS:
                try:
                    v = int(v) if key in AI_INT_FIELDS else float(v)
                except (TypeError, ValueError) as ex:
                    raise SettingsError(f"{key}: нужно число") from ex
            else:
                v = str(v).strip()
            out[key] = v
        return out

    def save_models(self, sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
        cfg = self._config()
        openai = cfg.get("openai")
        if not isinstance(openai, dict):
            openai = {}
        for key, values in sections.items():
            if key not in AI_TASK_KEYS:
                raise SettingsError(f"Неизвестная задача: {key}")
            clean = self._clean_ai_values(values or {})
            # Ключи, которых нет в форме (system_prompt и т.п.), сохраняем
            prev = openai.get(key) if isinstance(openai.get(key), dict) else {}
            kept = {k: v for k, v in prev.items() if k not in AI_FIELDS}
            merged = {**kept, **clean}
            if merged:
                openai[key] = merged
            else:
                openai.pop(key, None)
        if openai and "base_url" not in openai.get("default", {}):
            raise SettingsError("У основной модели должен быть адрес API (base_url)")
        if openai:
            cfg["openai"] = openai
        else:
            cfg.pop("openai", None)
        self._save(cfg)
        return self.read_models()

    def _effective_for_test(self, key: str, values: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config()
        # Сохранённая основная модель плюс то, что сейчас в форме: форма
        # важнее, а пустые поля берутся из конфига
        base = cfg.get_section("openai.default")
        own = self._clean_ai_values(values or {})
        settings = {**base, **own}
        if not settings.get("base_url"):
            raise SettingsError("Не задан адрес API (base_url)")
        return settings

    def list_remote_models(self, key: str, values: dict[str, Any]) -> list[str]:
        s = self._effective_for_test(key, values)
        headers = {"Authorization": f"Bearer {s['api_key']}"} if s.get("api_key") else {}
        try:
            r = requests.get(
                s["base_url"].rstrip("/") + "/models",
                headers=headers,
                timeout=(5, 20),
                proxies=get_proxies_dict(s.get("proxy_url")) if s.get("proxy_url") else None,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as ex:
            raise SettingsError(f"Не удалось получить список моделей: {ex}") from ex
        items = data.get("data") or data.get("models") or []
        names = [i.get("id") or i.get("name") for i in items if isinstance(i, dict)]
        return sorted(n for n in names if n)

    def test_model(self, key: str, values: dict[str, Any], image: bytes | None = None) -> dict[str, Any]:
        import time

        s = self._effective_for_test(key, values)
        client = ai.OpenAIClient(
            s["base_url"],
            api_key=s.get("api_key"),
            proxy_url=s.get("proxy_url"),
            max_retries=1,
        )
        kwargs = {
            "model": s.get("model"),
            "temperature": s.get("temperature"),
            "max_completion_tokens": s.get("max_completion_tokens") or 200,
            "timeout": s.get("timeout") or 60,
            "connect_timeout": s.get("connect_timeout") or 10,
        }
        started = time.monotonic()
        try:
            if image is not None:
                text = client.recognize_text(
                    "You read CAPTCHA images. Return ONLY the text from the image, exactly as it is written.",
                    image,
                    **kwargs,
                )
            else:
                text = client.complete(
                    "Ты помощник соискателя. Отвечай коротко, по-русски.",
                    "Ответь одним предложением: ты на связи?",
                    **kwargs,
                )
        except Exception as ex:  # noqa: BLE001
            raise SettingsError(f"Модель не ответила: {ex}") from ex
        return {
            "text": text,
            "seconds": round(time.monotonic() - started, 2),
            "model": s.get("model"),
            "base_url": s["base_url"],
        }

    # ---- письма --------------------------------------------------------

    def read_letters(self) -> dict[str, Any]:
        cfg = self._config()
        general = cfg.get_section("general")
        from ..operations import apply_vacancies as av

        return {
            "cover_letter": general.get("cover_letter", ""),
            "cover_letter_default": getattr(av, "DEFAULT_COVER_LETTER", ""),
            "cover_letter_prompt": general.get("cover_letter_prompt", ""),
            "cover_letter_prompt_default": getattr(av, "DEFAULT_COVER_LETTER_PROMPT", ""),
            "templates": general.get("cover_letter_templates", []),
            "placeholders": sorted(SAMPLE_PLACEHOLDERS),
            "ai_configured": bool(cfg.get("openai")),
            "has_comments": self.has_comments(),
        }

    def save_letters(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config()
        general = cfg.get("general")
        if not isinstance(general, dict):
            general = {}
        for key in ("cover_letter", "cover_letter_prompt"):
            value = payload.get(key)
            if value is None:
                continue
            value = str(value).replace("\r\n", "\n")
            if value.strip():
                self.preview(value)  # проверка плейсхолдеров
                general[key] = value
            else:
                general.pop(key, None)
        templates = payload.get("templates")
        if templates is not None:
            clean = []
            names = set()
            for t in templates:
                name = str(t.get("name") or "").strip()
                text = str(t.get("text") or "").replace("\r\n", "\n")
                if not name:
                    raise SettingsError("У каждого шаблона должно быть название")
                if name in names:
                    raise SettingsError(f"Название шаблона повторяется: {name}")
                if not text.strip():
                    raise SettingsError(f"Шаблон «{name}» пустой")
                self.preview(text)
                names.add(name)
                keywords = [str(k).strip() for k in t.get("keywords") or [] if str(k).strip()]
                clean.append({"name": name, "keywords": keywords, "text": text})
            if clean:
                general["cover_letter_templates"] = clean
            else:
                general.pop("cover_letter_templates", None)
        cfg["general"] = general
        self._save(cfg)
        return self.read_letters()

    # ---- промпты и тексты ------------------------------------------------

    def read_texts(self) -> dict[str, Any]:
        general = self._config().get_section("general")
        items = [{**t, "value": general.get(t["key"], "")} for t in _text_keys()]
        smtp = general.get("smtp") or {}
        return {
            "items": items,
            "smtp": {k: smtp.get(k) for k in ("host", "port", "user", "password", "ssl", "starttls")},
            "smtp_from": (self._config().get("smtp") or {}).get("from", ""),
            "has_comments": self.has_comments(),
        }

    def save_texts(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config()
        general = cfg.get("general") if isinstance(cfg.get("general"), dict) else {}
        keys = {t["key"]: t for t in _text_keys()}
        for key, value in (payload.get("values") or {}).items():
            if key not in keys:
                raise SettingsError(f"Неизвестный ключ: {key}")
            value = str(value or "").replace("\r\n", "\n")
            if value.strip() and value != keys[key]["default"]:
                general[key] = value
            else:
                general.pop(key, None)
        if "smtp" in payload:
            smtp = {k: v for k, v in (payload.get("smtp") or {}).items() if v not in (None, "")}
            if "port" in smtp:
                try:
                    smtp["port"] = int(smtp["port"])
                except (TypeError, ValueError) as ex:
                    raise SettingsError("Порт SMTP должен быть числом") from ex
            for flag in ("ssl", "starttls"):
                if flag in smtp:
                    smtp[flag] = bool(smtp[flag])
            if smtp.get("host"):
                general["smtp"] = smtp
            else:
                general.pop("smtp", None)
            # Адрес отправителя утилита читает из корневой секции [smtp]
            sender = str(payload.get("smtp_from") or "").strip()
            if sender:
                cfg["smtp"] = {**(cfg.get("smtp") or {}), "from": sender}
            elif isinstance(cfg.get("smtp"), dict):
                cfg["smtp"].pop("from", None)
                if not cfg["smtp"]:
                    cfg.pop("smtp")
        cfg["general"] = general
        self._save(cfg)
        return self.read_texts()

    def test_smtp(self, to: str) -> None:
        import smtplib
        from email.message import EmailMessage

        to = to.strip()
        if "@" not in to:
            raise SettingsError("Укажите адрес, куда отправить проверку")
        cfg = self._config()
        conf = cfg.get_section("general").get("smtp") or {}
        if not conf.get("host") or not conf.get("port"):
            raise SettingsError("Сначала сохраните сервер и порт SMTP")
        try:
            cls = smtplib.SMTP_SSL if conf.get("ssl") else smtplib.SMTP
            with cls(conf["host"], int(conf["port"]), timeout=20) as server:
                if not conf.get("ssl") and conf.get("starttls", True):
                    server.starttls()
                if conf.get("user") and conf.get("password"):
                    server.login(conf["user"], conf["password"])
                msg = EmailMessage()
                msg["Subject"] = "Проверка почты hh-applicant-tool"
                msg["From"] = (cfg.get("smtp") or {}).get("from") or conf.get("user") or to
                msg["To"] = to
                msg.set_content("Если вы читаете это письмо, отправка откликов на почту работает.")
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as ex:
            raise SettingsError(f"Письмо не отправилось: {ex}") from ex

    @staticmethod
    def preview_loose(template: str) -> str:
        values = _Loose({**SAMPLE_PLACEHOLDERS})
        try:
            return rand_text(template) % values
        except (ValueError, TypeError) as ex:
            raise SettingsError(f"Ошибка в шаблоне: {ex}") from ex

    @staticmethod
    def preview(template: str, placeholders: dict[str, str] | None = None) -> str:
        values = {**SAMPLE_PLACEHOLDERS, **(placeholders or {})}
        try:
            return render_template(rand_text(template), values, "шаблоне")
        except (ValueError, TypeError) as ex:
            raise SettingsError(str(ex)) from ex
