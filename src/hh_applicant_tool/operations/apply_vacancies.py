# Этот код, блять, уродлив. Просто ужасен
from __future__ import annotations

import argparse
import dataclasses
import html
import logging
import json
import random
import re
import time
from datetime import datetime
from email.message import EmailMessage
from itertools import chain
from typing import TYPE_CHECKING, Any, Iterator, NoReturn
from urllib.parse import (
    urlparse,
)

import requests

from ..ai.base import AIError
from ..ai.openai import OpenAIChat
from ..api import BadResponse, Redirect, datatypes
from ..api.datatypes import PaginatedItems, SearchVacancy, Vacancy
from ..api.errors import ApiError, LimitExceeded
from ..constants import GITHUB_REPO_URL
from ..errors import OperationError, XHRError
from ..tool import BaseNamespace, BaseOperation
from ..utils.datatypes import VacancyTestsData
from ..utils.string import (
    bool2str,
    choose_similar,
    rand_text,
    render_template,
    shorten,
    strip_tags,
)

if TYPE_CHECKING:
    from ..tool import HHApplicantTool


# Классы, имена которых начинаются с _, не импортируются при `import *`
@dataclasses.dataclass
class _ApplyState:
    """Изменяемое состояние рассылки для одного резюме."""

    applied_count: int = 0
    limit_reached: bool = False
    do_apply: bool = True
    site_emails: dict = dataclasses.field(default_factory=dict)


class _ApplyError(Exception):
    """Отклик на вакансию не отправлен. Вакансия пропускается."""


logger = logging.getLogger(__name__.split(".")[0])

# Параметры поиска вакансий, задаваемые флагами
SEARCH_PARAM_NAMES = (
    "text",
    "search_field",
    "order_by",
    "per_page",
    "experience",
    "schedule",
    "work_format",
    "employment",
    "area",
    "metro",
    "professional_role",
    "industry",
    "employer_id",
    "excluded_employer_id",
    "currency",
    "salary",
    "only_with_salary",
    "label",
    "period",
    "date_from",
    "date_to",
    "top_lat",
    "bottom_lat",
    "left_lng",
    "right_lng",
    "sort_point_lat",
    "sort_point_lng",
    "no_magic",
    "premium",
)

DEFAULT_PER_PAGE = 100
JOURNAL_FILENAME = "apply-journal.jsonl"

DEFAULT_COVER_LETTER = """\
{Здравствуйте|Добрый день}, меня зовут %(first_name)s.\
 {Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} на вакансию «%(vacancy_name)s».\
 С уважением, %(first_name)s."""


# Системные промпты - простые строки, не шаблоны
DEFAULT_COVER_LETTER_SYSTEM_PROMPT = """\
Ты пишешь короткий текст отклика на hh.ru от лица кандидата, от первого лица, по-русски. Рекрутер видит его раньше резюме и тратит на него 10 секунд.

Как писать:
1. Если в описании вакансии просят что-то написать в отклике (ответить на вопрос, указать кодовое слово, зарплату, ссылку), начни с ответа на это.
2. Первое предложение: «Здравствуйте!», кто ты и два-три твоих навыка из резюме, которые нужны в этой вакансии. Стаж называй, только если вакансия просит не меньше твоего; если она рассчитана на меньший опыт, стаж не упоминай. Если язык или фреймворк вакансии отсутствует в навыках резюме, не называй его и не пиши, что работал с ним. Если вакансия на родственном фреймворке (например, Vue или Angular при опыте React), можно одной фразой честно сказать, какой у тебя основной стек.
3. Дальше два факта из резюме, которые закрывают главные требования вакансии, лучше всего с цифрами из резюме.
4. Последняя строка: имя кандидата из резюме и его Telegram, если он есть в резюме.

Ограничения:
- Всего 2-4 предложения до подписи, не больше 450 знаков.
- Только факты из резюме. Чего нет в резюме, не упоминай.
- Пиши как человек в чате с рекрутером: просто, коротко, без пафоса. Названия технологий латиницей.
- Запрещено: «откликаюсь на позицию», «имею N лет опыта», «как указано в вакансии», «соответствует требованиям», «готов обсудить, как мой опыт поможет», «буду рад», «уверен», «ценный», «значительно», «эффективно», «успешно», «команда профессионалов», «динамично», «рассмотрите моё резюме», вопросы про зарплату.
- Не пересказывай вакансию и не хвали компанию.
- Выведи только текст: без markdown, списков, кавычек, темы письма и пояснений.

Пример тона для другого, вымышленного кандидата. Не бери из него ни имя, ни контакты, ни факты:
Здравствуйте! Пишу бэкенд на Go и PostgreSQL, 5 лет в коммерции. В платёжном сервисе переписал обработку вебхуков на очередь в Kafka, потерянных платежей стало ноль. Сейчас держу сервис на 3 тыс. RPS и сам веду деплой в Kubernetes.
Алексей, Telegram @alexgo"""

DEFAULT_AI_FILTER_SYSTEM_PROMPT = """\
Ты определяешь, подходит ли вакансия кандидату. Отвечай одним словом: «да» или «нет».
Требования к опыту и грейду выше, чем у кандидата (senior, lead, «от 6 лет», даже «от 10 лет»), не делают вакансию неподходящей. «Нет» по уровню только для стажировок и junior без опыта.
Родственные технологии считаются совпадением: Vue, Angular, Svelte при опыте React; Express, Fastify при опыте NestJS; MySQL при опыте PostgreSQL."""

DEFAULT_TEST_SYSTEM_PROMPT = """\
Ты заполняешь анкету работодателя при отклике на hh.ru от лица кандидата, от первого лица, по-русски. Ответы читает рекрутер.

Правила:
- Выводи только ответ: без вступлений, пояснений, markdown и плейсхолдеров в скобках.
- В вопросе с вариантами выведи только числовой ID выбранного варианта. Если в вопросе разрешено несколько ответов, выведи ID всех верных через запятую.
- Вопрос на знания (код, технологии, архитектура) решай как опытный инженер: выбирай технически верный вариант, даже если технологии нет в резюме. Опыт с технологией и знание ответа - разные вещи.
- Текстовый ответ: 1-2 коротких предложения по существу.
- Факты о кандидате бери только из резюме в запросе: город, формат работы, опыт, навыки, контакты. Контакт (Telegram, почту, телефон, GitHub) пиши, только если он дословно есть в резюме; иначе предложи связаться через hh.
- Если в резюме ответа нет, не утверждай ни «да», ни «нет» и не придумывай даты: ответь, что расскажешь подробно на собеседовании. Так отвечай про оформление по ТК на прошлых местах, дату выхода на работу, паспорт, адрес, судимость. Например: «Расскажу подробно на собеседовании».
- Стаж называй только тот, что следует из резюме. Если просят разделить опыт (фронтенд и бэкенд, React и Node.js), а в резюме роли общие, не придумывай разбивку: назови общий стаж и скажи, что во всех ролях работал и с тем, и с другим.
- Зарплата: если в резюме она указана, назови её; если нет, ответь, что готов обсудить на собеседовании после деталей задач.
- Вопросы о готовности (к тестовому, к проверке документов, к рекомендациям, к собеседованию) - отвечай положительно, если это не противоречит резюме.
- Релокация и переезд в другой город или страну: соглашайся, только если в резюме есть готовность к переезду. Иначе выбирай удалёнку или работу из своего города.
- Опыт с конкретной технологией, инструментом или библиотекой (Apollo, WebSockets, Kafka, n8n, low-code и т.п.) подтверждай, только если она названа в резюме. Если нет: в варианте выбирай «нет опыта» или ближайшее честное (например, «с другими инструментами», если в резюме есть родственная технология), в тексте честно скажи, что опыта нет, и назови близкое из резюме. «Расскажу на собеседовании» про опыт с технологиями не пиши: это только для личных и документальных фактов выше."""

# Пользовательские промпты - шаблоны, подстановка через %(name)s
DEFAULT_COVER_LETTER_PROMPT = """\
ВАКАНСИЯ
Название: %(vacancy_name)s
Компания: %(employer_name)s
Требуемый опыт: %(vacancy_experience)s
Ключевые навыки: %(vacancy_skills)s
Описание:
%(vacancy_description).3000s

РЕЗЮМЕ КАНДИДАТА
Имя: %(first_name)s
Должность: %(resume_title)s
Общий опыт: %(resume_total_experience)s
Навыки: %(resume_skills)s
О себе:
%(resume_about).1500s
Опыт работы:
%(resume_experience).2500s

Напиши сопроводительное письмо на эту вакансию."""

DEFAULT_AI_FILTER_PROMPT = """\
Название резюме: %(resume_title)s
Навыки, перечисленные в резюме: %(resume_skills)s
Опыт работы соискателя: %(resume_total_experience)s
Зарплата, указанная в резюме: %(resume_salary)s
Предпочитаемый соискателем формат работы: %(resume_work_format)s

========================================

Вакансия: %(vacancy_name)s
Навыки, требуемые в вакансии: %(vacancy_skills)s
Описание вакансии: %(vacancy_description).2000s

Подходит ли эта вакансия кандидату?"""

# Выбор варианта видит то же резюме, что и текстовый ответ: иначе на
# «опыт с PostgreSQL?» модель отвечала «нет», не видя опыта работы
_TEST_RESUME_BLOCK = """\
РЕЗЮМЕ
Имя: %(first_name)s
Должность: %(resume_title)s
Город: %(resume_city)s
Гражданство: %(resume_citizenship)s
Формат работы: %(resume_work_format)s
Тип занятости: %(resume_employment_form)s
Желаемая зарплата: %(resume_salary)s
Общий опыт: %(resume_total_experience)s
Образование: %(resume_education)s
Навыки: %(resume_skills)s
GitHub: %(github_profile_url)s
О себе:
%(resume_about).1200s
Опыт работы:
%(resume_experience).2000s

ВАКАНСИЯ: %(vacancy_name)s, %(employer_name)s
"""

DEFAULT_TEST_CHOICE_PROMPT = _TEST_RESUME_BLOCK + """
Вопрос: %(question)s
Варианты:
%(reply_options)s

Выбери вариант, который подходит кандидату, или правильный, если это вопрос на знания. Ответь только ID."""

DEFAULT_TEST_TEXT_PROMPT = _TEST_RESUME_BLOCK + """
Вопрос работодателя: %(question)s"""

TEST_MULTIPLE_HINT = "\n\nМожно выбрать несколько вариантов: выведи через запятую ID всех верных, а если верный один, только его."

DEFAULT_APPLY_MAIL_SUBJECT = "{Отклик|Резюме} на вакансию %(vacancy_name)s"
DEFAULT_APPLY_MAIL_BODY = "{Здравствуйте|Добрый день}, {прошу рассмотреть|пожалуйста рассмотрите} мое резюме %(resume_url)s на вакансию %(vacancy_name)s."


def pick_test_ids(answer: str, ids: list[str], multiple: bool) -> str | list[str]:
    """ID вариантов из ответа модели: только те, что реально есть в вопросе.
    Если ID не нашлось, берём самый похожий на ответ вариант."""
    found = list(dict.fromkeys(i for i in re.findall(r"\d+", answer) if i in ids))
    if not found:
        found = [choose_similar(answer.strip(". "), ids)]
    return found if multiple else found[0]


class Namespace(BaseNamespace):
    resume_id: str | None
    cover_letter: str | None
    force_cover_letter: bool
    ai_filter: bool | None
    ai_filter_system_prompt: str
    ai_filter_prompt: str
    cover_letter_system_prompt: str
    cover_letter_prompt: str
    dry_run: bool
    # Параметры поиска (None - не задано)
    text: str | None
    search_field: list[str] | None
    order_by: str | None
    per_page: int
    experience: str | None
    schedule: str | None
    work_format: list[str] | None
    employment: list[str] | None
    area: list[str] | None
    metro: list[str] | None
    professional_role: list[str] | None
    industry: list[str] | None
    employer_id: list[str] | None
    excluded_employer_id: list[str] | None
    currency: str | None
    salary: int | None
    only_with_salary: bool | None
    label: list[str] | None
    period: int | None
    date_from: str | None
    date_to: str | None
    top_lat: float | None
    bottom_lat: float | None
    left_lng: float | None
    right_lng: float | None
    sort_point_lat: float | None
    sort_point_lng: float | None
    no_magic: bool | None
    premium: bool | None
    total_pages: int
    excluded_filter: str | None
    max_responses: int
    send_email: bool
    skip_tests: bool
    letter_template: str | None


class Operation(BaseOperation):
    """Откликнуться на все подходящие вакансии."""

    __aliases__ = ("apply",)

    # Инициализируются в run()
    tool: HHApplicantTool
    cover_letter: str
    dry_run: bool
    excluded_pat: re.Pattern[str] | None
    force_cover_letter: bool | None
    max_responses: int | None
    resume_id: str | None
    send_email: bool | None
    skip_tests: bool | None
    total_pages: int

    # Шаблоны промптов и писем
    cover_letter_templates: list[dict[str, Any]]
    letter_template: dict[str, Any] | None
    cover_letter_prompt: str
    ai_filter_prompt: str
    test_choice_prompt: str
    test_text_prompt: str
    apply_mail_subject: str
    apply_mail_body: str

    # AI
    ai_filter: bool
    cover_letter_ai: OpenAIChat | None
    vacancy_filter_ai: OpenAIChat | None
    test_ai: OpenAIChat | None

    # Параметры поиска (проставляются через setattr из SEARCH_PARAM_NAMES)
    text: str | None
    search_field: list[str] | None
    order_by: str | None
    per_page: int
    experience: str | None
    schedule: str | None
    work_format: list[str] | None
    employment: list[str] | None
    area: list[str] | None
    metro: list[str] | None
    professional_role: list[str] | None
    industry: list[str] | None
    employer_id: list[str] | None
    excluded_employer_id: list[str] | None
    currency: str | None
    salary: int | None
    only_with_salary: bool | None
    label: list[str] | None
    period: int | None
    date_from: str | None
    date_to: str | None
    top_lat: float | None
    bottom_lat: float | None
    left_lng: float | None
    right_lng: float | None
    sort_point_lat: float | None
    sort_point_lng: float | None
    no_magic: bool | None
    premium: bool | None

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--resume-id", help="Идентефикатор резюме")
        parser.add_argument(
            "--force-cover-letter",
            "--force-letter",
            help="Всегда отправлять сопроводительное письмо при отклике, даже когда оно не требуется",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument(
            "--letter-template",
            "--template",
            help="Взять шаблон письма с этим названием из general.cover_letter_templates. Явно выбранный шаблон важнее AI и автоподбора по ключевым словам",
        )
        parser.add_argument(
            "--ai-filter",
            help="Использовать дополнительную фильтрацию вакансий через AI (вакансии непрошедшие фильтр попадают в черный список)",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--total-pages",
            "--pages",
            help="Количество обрабатываемых страниц поиска. Больше 2000 результатов API не отдает",  # noqa: E501
            default=20,
            type=int,
        )
        parser.add_argument(
            "--send-email",
            help="Отправлять письмо на email компании или рекрутера с просьбой рассмотреть резюме",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--skip-tests",
            help="Пропускать тесты при откликах вместо их решения",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--excluded-filter",
            type=str,
            help="Исключить вакансии, если название или описание не соответствует шаблону. Например, `--excluded-filter 'junior|стажир|bitrix|дружн\\w+ коллектив|полиграф|open\\s*space|опенспейс|хакатон|конкурс|тестов\\w+ задан'`. Вакансии попапвшие под фильтр попадают в черный список",
        )
        parser.add_argument(
            "--max-responses",
            type=int,
            help="Отправить не более N откликов (по умолчанию без ограничения)",
        )
        parser.add_argument(
            "--dry-run",
            help="Не отправлять отклики, а только выводить информацию",
            action=argparse.BooleanOptionalAction,
        )

        # Дальше идут параметры в точности соответствующие параметрам запроса
        # при поиске подходящих вакансий
        api_search_filters = parser.add_argument_group(
            "Фильтры для поиска вакансий",
            "Эти параметры напрямую соответствуют фильтрам поиска HeadHunter API",
        )

        api_search_filters.add_argument(
            "-s",
            "--search",
            dest="text",
            help="Строка поиска для фильтрации вакансий. Если указана, то поиск будет производиться по вакансиям. В остальных случаях отклики будут производиться по списку рекомендованных вакансий.",  # noqa: E501
            type=str,
        )
        api_search_filters.add_argument(
            "--per-page",
            help="Сколько должно быть результатов на странице",  # noqa: E501
            default=DEFAULT_PER_PAGE,
            type=int,
        )
        api_search_filters.add_argument(
            "--order-by",
            help="Сортировка вакансий",
            choices=[
                "publication_time",
                "salary_desc",
                "salary_asc",
                "relevance",
                "distance",
            ],
        )
        api_search_filters.add_argument(
            "--experience",
            help="Уровень опыта работы",
            choices=[
                "noExperience",
                "between1And3",
                "between3And6",
                "moreThan6",
            ],
        )
        api_search_filters.add_argument(
            "--schedule",
            help="Тип графика",
            choices=["fullDay", "shift", "flexible", "remote", "flyInFlyOut"],
        )
        api_search_filters.add_argument(
            "--work-format",
            nargs="+",
            help="Формат работы",
            choices=["REMOTE", "HYBRID", "ON_SITE", "FIELD_WORK"],
        )
        api_search_filters.add_argument(
            "--employment", nargs="+", help="Тип занятости"
        )
        api_search_filters.add_argument(
            "--area", nargs="+", help="Регион (area id)"
        )
        api_search_filters.add_argument(
            "--metro", nargs="+", help="Станции метро (metro id)"
        )
        api_search_filters.add_argument(
            "--professional-role", nargs="+", help="Проф. роль (id)"
        )
        api_search_filters.add_argument(
            "--industry", nargs="+", help="Индустрия (industry id)"
        )
        api_search_filters.add_argument(
            "--employer-id", nargs="+", help="ID работодателей"
        )
        api_search_filters.add_argument(
            "--excluded-employer-id", nargs="+", help="Исключить работодателей"
        )
        api_search_filters.add_argument(
            "--currency", help="Код валюты", choices=["RUR", "USD", "EUR"]
        )
        api_search_filters.add_argument(
            "--salary", type=int, help="Минимальная зарплата"
        )
        api_search_filters.add_argument(
            "--only-with-salary",
            action=argparse.BooleanOptionalAction,
            help="Только вакансии с указанной зарплатой",
        )
        api_search_filters.add_argument(
            "--label", nargs="+", help="Метки вакансий (label)"
        )
        api_search_filters.add_argument(
            "--period", type=int, help="Искать вакансии за N дней"
        )
        api_search_filters.add_argument(
            "--date-from", help="Дата публикации с (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--date-to", help="Дата публикации по (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--top-lat", type=float, help="Гео: верхняя широта"
        )
        api_search_filters.add_argument(
            "--bottom-lat", type=float, help="Гео: нижняя широта"
        )
        api_search_filters.add_argument(
            "--left-lng", type=float, help="Гео: левая долгота"
        )
        api_search_filters.add_argument(
            "--right-lng", type=float, help="Гео: правая долгота"
        )
        api_search_filters.add_argument(
            "--sort-point-lat",
            type=float,
            help="Координата lat для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--sort-point-lng",
            type=float,
            help="Координата lng для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--no-magic",
            action="store_true",
            help="Отключить авторазбор текста запроса",
        )
        api_search_filters.add_argument(
            "--premium",
            action=argparse.BooleanOptionalAction,
            help="Только премиум вакансии",
        )
        api_search_filters.add_argument(
            "--search-field",
            nargs="+",
            help="Поля поиска (name, company_name и т.п.)",
        )

    @property
    def api_client(self):
        return self.tool.api_client

    def run(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> None:
        self.tool = tool
        config = tool.general_config
        self.cover_letter = config.get("cover_letter", DEFAULT_COVER_LETTER)
        self.cover_letter_templates = [
            t
            for t in config.get("cover_letter_templates") or []
            if isinstance(t, dict) and t.get("text")
        ]
        self.letter_template = None
        if args.letter_template:
            self.letter_template = next(
                (
                    t
                    for t in self.cover_letter_templates
                    if t.get("name") == args.letter_template
                ),
                None,
            )
            if self.letter_template is None:
                names = ", ".join(
                    str(t.get("name")) for t in self.cover_letter_templates
                )
                raise OperationError(
                    f"Шаблон письма {args.letter_template!r} не найден. "
                    f"Есть: {names or 'шаблонов нет'}"
                )
        for name in SEARCH_PARAM_NAMES:
            setattr(self, name, getattr(args, name))
        self.dry_run = args.dry_run
        self._rec: dict | None = None
        self.excluded_pat = None
        if args.excluded_filter:
            try:
                self.excluded_pat = re.compile(
                    args.excluded_filter, re.IGNORECASE
                )
            except re.error as ex:
                raise OperationError(
                    f"Некорректное регулярное выражение в --excluded-filter: {ex}"
                ) from ex
        self.force_cover_letter = args.force_cover_letter
        self.max_responses = args.max_responses
        self.resume_id = args.resume_id
        self.send_email = args.send_email
        self.skip_tests = args.skip_tests
        self.total_pages = args.total_pages

        # Приоритет: аргумент командной строки -> конфиг -> дефолтная константа
        # Системные промпты - обычный текст, пользовательские - шаблоны
        self.cover_letter_prompt = config.get(
            "cover_letter_prompt", DEFAULT_COVER_LETTER_PROMPT
        )
        self.ai_filter_prompt = config.get(
            "ai_filter_prompt", DEFAULT_AI_FILTER_PROMPT
        )
        self.test_choice_prompt = config.get(
            "test_choice_prompt", DEFAULT_TEST_CHOICE_PROMPT
        )
        self.test_text_prompt = config.get(
            "test_text_prompt", DEFAULT_TEST_TEXT_PROMPT
        )
        self.ai_filter = bool(args.ai_filter)

        # ai_enabled учитывает и --use-ai, и наличие секции [openai] в
        # конфиге. Клиенты берутся через кеш tool.ai_client_for: значения
        # из конфига имеют приоритет над дефолтами
        self.cover_letter_ai = self.vacancy_filter_ai = self.test_ai = None
        if tool.ai_enabled:
            self.cover_letter_ai = tool.ai_chat_for(
                "openai.cover_letter",
                system_prompt=DEFAULT_COVER_LETTER_SYSTEM_PROMPT,
                temperature=0.3,
            )
            self.test_ai = tool.ai_chat_for(
                "openai.test_solver",
                system_prompt=DEFAULT_TEST_SYSTEM_PROMPT,
                temperature=0.2,
            )
            if self.ai_filter:
                self.vacancy_filter_ai = tool.ai_chat_for(
                    "openai.vacancy_filter",
                    system_prompt=DEFAULT_AI_FILTER_SYSTEM_PROMPT,
                    temperature=0.0,
                )

        self.apply_mail_subject = config.get(
            "apply_mail_subject", DEFAULT_APPLY_MAIL_SUBJECT
        )
        self.apply_mail_body = config.get(
            "apply_mail_body", DEFAULT_APPLY_MAIL_BODY
        )

        self._apply_vacancies()

    # Если AI ответил что-то непонятное, лучше не отбрасывать вакансию
    @staticmethod
    def _parse_is_suitable_response(response: str) -> bool:
        answer = response.strip().lower().strip(" .!\"'«»")

        if re.match(r"(нет?|no|false)\b", answer):
            return False

        return True

    def _is_vacancy_suitable(
        self, vacancy: dict, placeholders: dict[str, str]
    ) -> bool:
        if not self.vacancy_filter_ai:
            return True

        self._add_vacancy_details(vacancy, placeholders)
        prompt = render_template(
            self.ai_filter_prompt, placeholders, "промпта AI-фильтра"
        )
        response, meta = self._ai_call(
            self.vacancy_filter_ai, prompt, "фильтр"
        )
        logger.debug("AI ответ фильтра: %s", response)
        suitable = self._parse_is_suitable_response(response)
        self._note(
            vacancy_experience=placeholders.get("vacancy_experience"),
            vacancy_skills=placeholders.get("vacancy_skills"),
            ai_filter={"suitable": suitable, **meta},
        )
        return suitable

    def _add_vacancy_details(self, vacancy: dict, placeholders: dict) -> None:
        """Догружает полную вакансию в плейсхолдеры: описание, навыки, опыт.
        Фильтр и письмо получают один и тот же словарь, запрос идет один раз."""
        if "vacancy_description" in placeholders:
            return
        # В выдаче поиска описания нет, полная вакансия приходит только по id
        full_vacancy: Vacancy = (
            vacancy
            if "description" in vacancy
            else self.api_client.get(f"/vacancies/{vacancy['id']}")
        )
        placeholders["vacancy_description"] = strip_tags(
            full_vacancy.get("description") or ""
        )
        placeholders["vacancy_skills"] = ", ".join(
            s["name"]
            for s in (full_vacancy.get("key_skills") or [])
            if s.get("name")
        )
        placeholders["vacancy_experience"] = (
            full_vacancy.get("experience") or {}
        ).get("name") or ""

    def _apply_vacancies(self) -> None:
        resumes: list[datatypes.Resume] = self.tool.get_resumes()

        # Выбираем только опубликованные
        resumes = [x for x in resumes if x["status"]["id"] == "published"]

        if not resumes:
            raise OperationError("У вас нет опубликованных резюме")

        if self.resume_id:
            resumes = [x for x in resumes if x["id"] == self.resume_id]
            if not resumes:
                raise OperationError(f"Резюме {self.resume_id} не найдено")

        me: datatypes.User = self.tool.get_me()
        seen_employers = set()

        for resume in resumes:
            limit_reached = self._apply_resume(
                resume=resume,
                user=me,
                seen_employers=seen_employers,
            )
            if limit_reached:
                logger.info(
                    "Лимит откликов hh.ru исчерпан. Пропускаю оставшиеся резюме."
                )
                print("⛔ Лимит откликов hh.ru исчерпан. Попробуйте позже.")
                break

        logger.info("Отклики разосланы!")
        print("📝 Отклики на вакансии разосланы!")

    def _apply_resume(
        self,
        resume: datatypes.Resume,
        user: datatypes.User,
        seen_employers: set[str],
    ) -> bool:
        logger.info(
            "Начинаю рассылку откликов для резюме: %s (%s)",
            resume["alternate_url"],
            resume["title"],
        )
        print("🚀 Начинаю рассылку откликов для резюме:", resume["title"])

        state = _ApplyState()
        placeholders = self._build_resume_placeholders(resume, user)

        for vacancy in self._get_vacancies(resume_id=resume["id"]):
            if not state.do_apply or self._max_responses_reached(state):
                break
            self._rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "resume_id": resume["id"],
                "vacancy_id": vacancy["id"],
                "vacancy": vacancy.get("name"),
                "employer": (vacancy.get("employer") or {}).get("name"),
                "url": vacancy.get("alternate_url"),
                "has_test": bool(vacancy.get("has_test")),
                "letter_required": bool(vacancy.get("response_letter_required")),
                "dry_run": self.dry_run,
            }
            # Любая ошибка отклика (и вакансии в целом) здесь превращается
            # в пропуск вакансии, приложение не падает
            try:
                self._process_vacancy(
                    vacancy, resume, placeholders, seen_employers, state
                )
            except LimitExceeded:
                state.do_apply = False
                state.limit_reached = True
                self._note(result="limit")
                break
            except Redirect:
                self._note(result="redirect")
                logger.warning(
                    "Игнорирую перенаправление на форму: %s",
                    vacancy["alternate_url"],
                )
            except (ApiError, XHRError, _ApplyError) as ex:
                self._note(result="error", error=str(ex)[:500])
                logger.warning(
                    "Пропускаю вакансию %s: %s", vacancy["alternate_url"], ex
                )
            except (
                BadResponse,
                AIError,
                ValueError,
                requests.RequestException,
            ) as ex:
                self._note(result="error", error=str(ex)[:500])
                logger.error(
                    "Пропускаю вакансию %s: %s", vacancy["alternate_url"], ex
                )
            finally:
                self._write_journal()

        logger.info(
            "Закончили рассылку откликов для резюме: %s (%s). Отправлено: %d",
            resume["alternate_url"],
            resume["title"],
            state.applied_count,
        )
        print(
            f"✅️ Закончили рассылку для резюме: {resume['title']}. "
            f"Отправлено: {state.applied_count}"
        )
        return state.limit_reached

    def _build_resume_placeholders(
        self, resume: datatypes.Resume, user: datatypes.User
    ) -> dict:
        placeholders = {
            # Могут быть перезаписаны значениями из резюме
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
            "resume_hash": resume.get("id") or "",
            "resume_title": resume.get("title") or "",
            "resume_url": resume.get("alternate_url") or "",
            # Своя ссылка задаётся в конфиге, чужую подставлять нельзя
            "github_profile_url": self.tool.general_config.get(
                "github_profile_url", ""
            ),
        }
        placeholders.update(
            self.tool.get_resume_placeholders(resume.get("id")),
        )
        return placeholders

    def _note(self, **fields: Any) -> None:
        if getattr(self, "_rec", None) is not None:
            self._rec.update(fields)

    def _write_journal(self) -> None:
        """Журнал решений по вакансиям для статистики: одна JSON-строка на
        вакансию, до которой дошли фильтр, письмо или отклик."""
        rec, self._rec = self._rec, None
        if not rec or "result" not in rec:
            return
        try:
            path = self.tool.config_dir_path / JOURNAL_FILENAME
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as ex:
            logger.warning("Не записал журнал откликов: %s", ex)

    def _ai_call(
        self, chat: OpenAIChat, prompt: str, what: str
    ) -> tuple[str, dict]:
        """Запрос к модели + сведения для журнала: модель и время.
        Строка в выводе, чтобы в логе задачи было видно работу модели."""
        start = time.monotonic()
        answer = chat.complete(prompt)
        meta = {
            "model": chat.model or chat.client.model,
            "sec": round(time.monotonic() - start, 1),
            "system": chat.system_prompt,
            "prompt": prompt,
            "answer": answer,
        }
        brief = shorten(" ".join(answer.split()), 60)
        print(f"🧠 {meta['model']} · {what}: {brief} · {meta['sec']} с")
        return answer, meta

    def _max_responses_reached(self, state: _ApplyState) -> bool:
        if self.max_responses and state.applied_count >= self.max_responses:
            logger.info(
                "Достигнут лимит откликов --max-responses (%d). Останавливаюсь.",
                self.max_responses,
            )
            return True
        return False

    def _process_vacancy(
        self,
        vacancy: dict,
        resume: datatypes.Resume,
        placeholders: dict,
        seen_employers: set[str],
        state: _ApplyState,
    ) -> None:
        employer = vacancy.get("employer", {})
        message_placeholders = {
            "vacancy_name": vacancy.get("name", ""),
            "vacancy_url": vacancy.get("alternate_url") or "",
            "employer_name": employer.get("name", ""),
            **placeholders,
        }

        # По факту контакты можно получить только здесь?!
        if vacancy.get("contacts"):
            logger.debug(
                f"Найдены контакты в вакансии: {vacancy['alternate_url']}"
            )

        if not state.do_apply or self._should_skip_vacancy(vacancy):
            return
        if self._is_vacancy_rejected(vacancy, message_placeholders):
            return

        self._load_employer(employer, seen_employers, state)
        letter = self._build_cover_letter(vacancy, message_placeholders)

        logger.debug(
            "Пробуем откликнуться на вакансию: %s", vacancy["alternate_url"]
        )

        if self.dry_run:
            self._note(result="dry_run")
            logger.info(
                "[dry-run] Отклик не отправляется: %s", vacancy["alternate_url"]
            )
            return

        # Если отклик не отправлен, функции ниже бросают исключение, и до
        # отправки письма дело не доходит
        if vacancy.get("has_test"):
            self._apply_with_test(
                vacancy, resume, letter, message_placeholders, state
            )
        else:
            self._send_negotiation(vacancy, resume, letter, state)

        if self.send_email:
            self._send_apply_email(
                vacancy,
                employer.get("id"),
                message_placeholders,
                state,
            )

    def _should_skip_vacancy(self, vacancy: dict) -> bool:
        """Пропускаем вакансии, на которые нельзя или не нужно откликаться."""
        url = vacancy["alternate_url"]

        if relations := vacancy.get("relations", []):
            if "got_rejection" in relations:
                logger.debug("Вы получили отказ от %s", url)
                print("⛔ Пришел отказ от", url)
            logger.debug("Пропускаем вакансию с откликом: %s", url)
            return True

        if vacancy.get("archived"):
            logger.debug("Пропускаем вакансию в архиве: %s", url)
            return True

        if vacancy.get("has_test") and self.skip_tests:
            logger.debug("Пропускаю вакансию с тестом %s", url)
            return True

        if redirect_url := vacancy.get("response_url"):
            logger.debug(
                "Пропускаем вакансию %s с перенаправлением: %s",
                url,
                redirect_url,
            )
            return True

        return False

    def _blacklist_vacancy(self, vacancy: dict) -> None:
        if not self.dry_run:
            self.api_client.put(f"/vacancies/blacklisted/{vacancy['id']}")

    def _is_vacancy_rejected(
        self, vacancy: dict, message_placeholders: dict
    ) -> bool:
        """Обычные фильтры + AI фильтр. True — вакансия отброшена."""
        url = vacancy["alternate_url"]

        if self._is_excluded(vacancy):
            self._note(result="excluded")
            logger.info("Вакансия попала под фильтр: %s", url)
            self._blacklist_vacancy(vacancy)
            if not self.dry_run:
                logger.info("Вакансия добавлена в черный список: %s", url)
            return True

        if (
            self.ai_filter
            and self.vacancy_filter_ai
            and not self._is_vacancy_suitable(vacancy, message_placeholders)
        ):
            self._note(result="ai_rejected")
            logger.info("Вакансия отклонена AI фильтром: %s", url)
            print("🧠 AI посчитал неподходящей", url)
            self._blacklist_vacancy(vacancy)
            return True

        return False

    def _load_employer(
        self, employer: dict, seen_employers: set[str], state: _ApplyState
    ) -> None:
        """Перед откликом выгружаем профиль компании."""
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return

        employer_profile: datatypes.Employer = self.api_client.get(
            f"/employers/{employer_id}"
        )

        # Какая блядь эту строку стерла?
        seen_employers.add(employer_id)

        self._collect_site_emails(employer_profile, employer_id, state)

    def _collect_site_emails(
        self,
        employer_profile: datatypes.Employer,
        employer_id: str,
        state: _ApplyState,
    ) -> None:
        """Если есть сайт, то ищем на нем емейлы для отправки письма."""
        if not self.send_email:
            return
        site_url = (employer_profile.get("site_url") or "").strip()
        if not site_url:
            return

        if "://" not in site_url:
            site_url = "https://" + site_url
        logger.debug("visit site: %s", site_url)

        # Недоступный сайт работодателя не должен мешать отклику
        try:
            site_info = self._parse_site(site_url)
            logger.debug(site_info)
            state.site_emails[employer_id] = site_info["emails"]
        except requests.RequestException as ex:
            logger.error(ex)
            return

        if site_info:
            logger.debug("site info: %r", site_info)

    def _build_cover_letter(
        self, vacancy: dict, message_placeholders: dict
    ) -> str:
        if not (
            self.force_cover_letter or vacancy.get("response_letter_required")
        ):
            return ""

        if self.letter_template:
            letter = render_template(
                rand_text(self.letter_template["text"]),
                message_placeholders,
                f"шаблоне письма {self.letter_template.get('name')!r}",
            )
        elif self.cover_letter_ai:
            self._add_vacancy_details(vacancy, message_placeholders)
            prompt = render_template(
                self.cover_letter_prompt,
                message_placeholders,
                "промпта сопроводительного письма",
            )
            logger.debug("prompt: %s", prompt)
            letter, meta = self._ai_call(
                self.cover_letter_ai, prompt, "письмо"
            )
            self._note(letter_ai=meta)
        else:
            template = self._match_letter_template(vacancy)
            letter = render_template(
                rand_text(template["text"] if template else self.cover_letter),
                message_placeholders,
                "сопроводительного письма",
            )

        logger.debug("Сопроводительное письмо: %.255s", letter)
        self._note(letter=letter)
        return letter

    def _match_letter_template(self, vacancy: dict) -> dict[str, Any] | None:
        """Первый шаблон, чьё ключевое слово есть в названии или описании
        вакансии. Шаблон без ключевых слов - запасной."""
        if not self.cover_letter_templates:
            return None
        snippet = vacancy.get("snippet") or {}
        haystack = " ".join(
            filter(
                None,
                [
                    vacancy.get("name"),
                    snippet.get("requirement"),
                    snippet.get("responsibility"),
                    vacancy.get("description"),
                ],
            )
        ).lower()
        fallback = None
        for t in self.cover_letter_templates:
            keywords = [
                str(k).strip().lower() for k in t.get("keywords") or [] if str(k).strip()
            ]
            if not keywords:
                fallback = fallback or t
                continue
            if any(k in haystack for k in keywords):
                logger.debug("Шаблон письма по ключевому слову: %s", t.get("name"))
                return t
        return fallback

    def _apply_with_test(
        self,
        vacancy: dict,
        resume: datatypes.Resume,
        letter: str,
        message_placeholders: dict,
        state: _ApplyState,
    ) -> None:
        """Отклик на вакансию с тестом. При неудаче бросает исключение."""
        logger.debug("Решаем тест: %s", vacancy["alternate_url"])
        solved_tests, response_url = self._solve_vacancy_test(
            vacancy_id=vacancy["id"],
            resume_hash=resume["id"],
            letter=letter,
            placeholders=message_placeholders,
        )
        self._process_tests(solved_tests, response_url, vacancy, state)

    def _process_tests(
        self,
        solved_tests: dict,
        vacancy_response_url: str,
        vacancy: dict,
        state: _ApplyState,
    ) -> None:
        """Отправляем решённый тест (капчу решает xhr_request).

        Возвращает управление только если отклик отправлен.
        """
        result = self._send_vacancy_test(
            solved_tests, referer_url=vacancy_response_url
        )

        if str(result.get("success")).lower() == "true":
            self._note(result="sent")
            state.applied_count += 1
            logger.info(
                "Отправили отклик на вакансию с тестом: %s",
                vacancy["alternate_url"],
            )
            print(
                "📨 Отправили отклик на вакансию с тестом",
                vacancy["alternate_url"],
            )
            return

        self._handle_test_error(result, state)

    def _handle_test_error(self, result: dict, state: _ApplyState) -> NoReturn:
        if result.get("error") == "negotiations-limit-exceeded":
            state.do_apply = False
            state.limit_reached = True
            raise _ApplyError("Достигнут лимит откликов")

        raise _ApplyError(
            f"Ошибка при отклике на вакансию с тестом: {shorten(str(result), 300)}"
        )

    def _send_negotiation(
        self,
        vacancy: dict,
        resume: datatypes.Resume,
        letter: str,
        state: _ApplyState,
    ) -> None:
        """Обычный отклик.

        Возвращает управление только если отклик отправлен. Redirect (hh
        перенаправил на форму) и остальные ошибки уходят наверх.
        """
        res = self.api_client.post(
            "/negotiations",
            {
                "resume_id": resume["id"],
                "vacancy_id": vacancy["id"],
                "message": letter,
            },
            delay=random.uniform(1, 3),
        )
        if res != {}:
            raise _ApplyError(f"Неожиданный ответ на отклик: {res!r:.300}")

        self._note(result="sent")
        state.applied_count += 1
        logger.info(
            "Отправили отклик на вакансию без теста: %s",
            vacancy["alternate_url"],
        )
        print("📨 Отправили отклик на вакансию", vacancy["alternate_url"])

    def _send_apply_email(
        self,
        vacancy: dict,
        employer_id: str | None,
        message_placeholders: dict,
        state: _ApplyState,
    ) -> None:
        """Отправка письма на email работодателя."""
        # contacts может быть null
        mail_to = (vacancy.get("contacts") or {}).get("email") or (
            state.site_emails.get(employer_id)
        )
        if not mail_to:
            return
        if isinstance(mail_to, list):
            mail_to = ", ".join(mail_to)

        mail_subject = render_template(
            self.apply_mail_subject,
            message_placeholders,
            "apply_mail_subject",
            randomize_text=True,
        )
        mail_body = render_template(
            self.apply_mail_body,
            message_placeholders,
            "apply_mail_body",
            randomize_text=True,
            escape_html=True,
        )
        # Отклик уже отправлен, ошибка SMTP не должна считаться его провалом
        try:
            self._send_email(mail_to, mail_subject, mail_body)
            print(
                "📧 Отправлено письмо на email по поводу вакансии",
                vacancy["alternate_url"],
            )
        except Exception as ex:
            logger.error(f"Ошибка отправки письма: {ex}")

    def _send_email(self, to: str, subject: str, body: str) -> None:
        cfg = self.tool.config.get("smtp", {})
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from") or cfg.get("user")
        msg["To"] = to
        msg.set_content(body, subtype="html")
        self.tool.smtp.send_message(msg)

    def _get_vacancy_tests(self, response_url: str) -> VacancyTestsData | None:
        """Парсит тесты"""
        res = self.tool.get_initial_state(response_url)
        return res.get("vacancyTests")

    def _solve_vacancy_test(
        self,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
        placeholders: dict[str, str] | None = None,
    ) -> tuple[dict, str]:
        """Загружает тест и решает его.

        Возвращает payload для отправки и URL страницы отклика (referer).
        """
        placeholders = placeholders or {}
        response_url = f"{self.tool.base_url}/applicant/vacancy_response?vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"

        if not (tests_data := self._get_vacancy_tests(response_url)):
            raise ValueError(f"Данные тестов не найдены на {response_url}.")

        if not (test_data := tests_data.get(str(vacancy_id))):
            raise ValueError(
                f"Пустые данные теста вакансии vacancy_id={vacancy_id}"
            )

        logger.debug(f"{test_data = }")

        payload: dict[str, Any] = {
            "_xsrf": self.tool.xsrf_token,
            "uidPk": test_data["uidPk"],
            "guid": test_data["guid"],
            "startTime": test_data["startTime"],
            "testRequired": test_data["required"],
            "vacancy_id": vacancy_id,
            "resume_hash": resume_hash,
            "ignore_postponed": "true",
            "incomplete": "false",
            "mark_applicant_visible_in_vacancy_country": "false",
            "country_ids": "[]",
            "lux": "true",
            "withoutTest": "no",
            "letter": letter,
        }

        qa: list[dict] = []
        for task in test_data["tasks"]:
            field_name = f"task_{task['id']}"
            solutions = task.get("candidateSolutions") or []
            question = (task.get("description") or "").strip()
            meta: dict = {}

            if solutions:
                if self.test_ai:
                    options = "\n".join(
                        f"{s['id']}: {strip_tags(s['text'])}" for s in solutions
                    )
                    prompt = render_template(
                        self.test_choice_prompt,
                        {
                            **placeholders,
                            "question": question,
                            "reply_options": options,
                        },
                        "промпта выбора ответа в тесте",
                    )
                    multiple = str(task.get("multiple")).lower() == "true"
                    if multiple:
                        prompt += TEST_MULTIPLE_HINT
                    answer, meta = self._ai_call(self.test_ai, prompt, "тест")
                    logger.debug("AI ответ (выбор): %.255s", answer)
                    payload[field_name] = pick_test_ids(
                        answer, [str(s["id"]) for s in solutions], multiple
                    )
                else:
                    texts = [strip_tags(s["text"]) for s in solutions]
                    best = choose_similar("да", texts)

                    # choose_similar возвращает строку (текст варианта) или None.
                    # Находим соответствующее решение по индексу.
                    if best is not None and best in texts:
                        chosen = solutions[texts.index(best)]
                    else:
                        # По статистике правильный ответ в большинстве случаев
                        # находится посередине
                        chosen = solutions[len(solutions) // 2]

                    payload[field_name] = chosen["id"]
                by_id = {str(s["id"]): strip_tags(s["text"]) for s in solutions}
                # meta первым: в нём сырой ответ модели (ID), а в журнал
                # нужен текст выбранного варианта
                chosen_ids = payload[field_name]
                if not isinstance(chosen_ids, list):
                    chosen_ids = [chosen_ids]
                qa.append({
                    **meta,
                    "q": question[:500],
                    "options": list(by_id.values()),
                    "answer": "; ".join(by_id.get(str(i), str(i)) for i in chosen_ids),
                })
            else:
                # Рандомные эмоджи
                # payload[f"{field_name}_text"] = "".join(
                #     chr(random.randint(0x1F300, 0x1F64F))
                #     for _ in range(random.randint(3, 15))
                # )

                if self.test_ai:
                    prompt = render_template(
                        self.test_text_prompt,
                        {**placeholders, "question": question},
                        "промпта текстового ответа в тесте",
                    )
                    answer, meta = self._ai_call(self.test_ai, prompt, "тест")
                    ## добавляем ответ AI на вопрос теста##
                    logger.debug("AI ответ: %.255s", answer)
                elif "://" in question:
                    answer = rand_text(
                        "{{Простите|Извините}, но я не перехожу по {внешним|сторонним} ссылкам, так как {опасаюсь взлома|не хочу {быть взломанным|подхватить вирус|чтобы у меня {со|с банковского} счета украли деньги}}.|У меня нет времени на заполнение анкет и гуглодоков}"
                    )
                # Тупоеблые любят вопросы с ответами да/нет, где ответ да является правильным в большинстве случаев.
                else:
                    answer = "Да"

                payload[f"{field_name}_text"] = answer
                qa.append({**meta, "q": question[:500], "answer": answer})

        self._note(test=qa)
        logger.debug(f"{payload = }")
        return payload, response_url

    def _send_vacancy_test(
        self,
        payload: dict,
        referer_url: str,
    ) -> dict:
        """Отправляет решённый тест и возвращает JSON-ответ hh."""
        return self.tool.xhr_request(
            "POST",
            "/applicant/vacancy_response/popup",
            data=payload,
            referer_url=referer_url,
            additional_headers={
                "X-Hhtmfrom": "vacancy",
                "X-Hhtmsource": "vacancy_response",
            },
        )  # ty: ignore[invalid-return-type]

    def _parse_site(self, url: str) -> dict[str, Any]:
        with self.tool.session.get(
            url,
            timeout=(10, 30),
            headers={"Referer": GITHUB_REPO_URL},
        ) as r:
            val = lambda m: html.unescape(m.group(1)) if m else ""

            title = val(re.search(r"<title>(.*?)</title>", r.text, re.I | re.S))
            description = val(
                re.search(
                    r'<meta name="description" content="(.*?)"', r.text, re.I
                )
            )
            generator = val(
                re.search(
                    r'<meta name="generator" content="(.*?)"', r.text, re.I
                )
            )

            # Поиск email
            emails = set(
                m.group(0)
                # Исключение всякого мусора типа energy-software-slider-225x225@2x.png
                for m in re.finditer(
                    r"\b[a-z][a-z0-9_.-]+@([a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
                    r.text,
                )
            )

            return {
                "title": title,
                "description": description,
                "generator": generator,
                "emails": list(emails),
                "server_name": r.headers.get("Server"),
                "powered_by": r.headers.get("X-Powered-By"),
                # Не работает, если отключена проверка сертификата
                "ip_address": r.raw._connection.sock.getpeername()[0]
                if r.raw._connection
                else None,
            }

    # Слишком тормознутая... Толи российские айпи заблокированы
    def _get_subdomains(self, url: str) -> set[str]:
        domain = urlparse(url).netloc
        r = self.tool.session.get(
            "https://crt.sh",
            params={"q": domain, "output": "json"},
            timeout=30,
        )

        r.raise_for_status()

        return set(
            item
            for item in chain.from_iterable(
                item["name_value"].split() for item in r.json()
            )
            if not item.startswith("*.")
        )

    def _get_search_params(self, page: int) -> dict:
        params = {
            **({"page": page} if page else {}),
            "per_page": self.per_page,
        }
        if self.order_by:
            params |= {"order_by": self.order_by}
        if self.schedule:
            params["schedule"] = self.schedule
        if self.work_format:
            params["work_format"] = list(self.work_format)
        if self.experience:
            params["experience"] = self.experience
        if self.currency:
            params["currency"] = self.currency
        if self.salary:
            params["salary"] = self.salary
        if self.period:
            params["period"] = self.period
        if self.date_from:
            params["date_from"] = self.date_from
        if self.date_to:
            params["date_to"] = self.date_to
        if self.top_lat:
            params["top_lat"] = self.top_lat
        if self.bottom_lat:
            params["bottom_lat"] = self.bottom_lat
        if self.left_lng:
            params["left_lng"] = self.left_lng
        if self.right_lng:
            params["right_lng"] = self.right_lng
        if self.sort_point_lat:
            params["sort_point_lat"] = self.sort_point_lat
        if self.sort_point_lng:
            params["sort_point_lng"] = self.sort_point_lng
        if self.search_field:
            params["search_field"] = list(self.search_field)
        if self.employment:
            params["employment"] = list(self.employment)
        if self.area:
            params["area"] = list(self.area)
        if self.metro:
            params["metro"] = list(self.metro)
        if self.professional_role:
            params["professional_role"] = list(self.professional_role)
        if self.industry:
            params["industry"] = list(self.industry)
        if self.employer_id:
            params["employer_id"] = list(self.employer_id)
        if self.excluded_employer_id:
            params["excluded_employer_id"] = list(self.excluded_employer_id)
        if self.label:
            params["label"] = list(self.label)
        if self.only_with_salary:
            params["only_with_salary"] = bool2str(self.only_with_salary)
        # if self.clusters:
        #     params["clusters"] = bool2str(self.clusters)
        if self.no_magic:
            params["no_magic"] = bool2str(self.no_magic)
        if self.premium:
            params["premium"] = bool2str(self.premium)
        # if self.responses_count_enabled is not None:
        #     params["responses_count_enabled"] = bool2str(self.responses_count_enabled)

        return params

    def _get_vacancies(
        self,
        resume_id: str,
    ) -> Iterator[SearchVacancy]:
        for page in range(self.total_pages):
            logger.debug(f"Загружаем вакансии со страницы: {page + 1}")
            params = self._get_search_params(page)

            logger.debug(params)

            if self.text:
                search_endpoint = "/vacancies"
                params |= {"text": self.text}
            else:
                search_endpoint = f"/resumes/{resume_id}/similar_vacancies"
            res: PaginatedItems[SearchVacancy] = self.api_client.get(
                search_endpoint,
                params,
            )
            logger.debug(f"Количество вакансий: {res['found']}")

            if not res["items"]:
                return

            yield from res["items"]

            if page >= res["pages"] - 1:
                return

    def _is_excluded(self, vacancy: SearchVacancy) -> bool:
        if not self.excluded_pat:
            return False

        snippet = vacancy.get("snippet", {})
        vacancy_summary = " ".join(
            filter(
                None,
                [
                    vacancy.get("name"),
                    snippet.get("requirement"),
                    snippet.get("responsibility"),
                ],
            )
        )

        logger.debug("vacancy summary: %s", vacancy_summary)

        if self.excluded_pat.search(vacancy_summary):
            return True

        # Когда мы грузим страницу через API, то можем перехватить капчу, а на
        # самом сайте нет
        res = self.api_client.get(f"/vacancies/{vacancy['id']}")
        description = res["description"]
        logger.debug("vacancy description: %.255s", description)
        return bool(self.excluded_pat.search(description))
