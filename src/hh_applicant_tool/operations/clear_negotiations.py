from __future__ import annotations

import argparse
import datetime as dt
import logging
from typing import TYPE_CHECKING

from ..api.errors import ApiError
from ..tool import BaseNamespace, BaseOperation
from ..utils.date import parse_api_datetime

if TYPE_CHECKING:
    from ..tool import HHApplicantTool

logger = logging.getLogger(__name__.split(".")[0])


class Namespace(BaseNamespace):
    cleanup: bool
    blacklist_discard: bool
    older_than: int
    hide_stale: int | None
    dry_run: bool
    delete_chat: bool
    block_ats: bool


def stale_responses(
    negotiations, days: int, now: dt.datetime | None = None
) -> list[dict]:
    """Отклики без реакции работодателя (state=response), ещё не скрытые
    и без движения дольше days дней. Приглашения и отказы не попадают."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return [
        n
        for n in negotiations
        if n["state"]["id"] == "response"
        and not n.get("hidden")
        and (now - parse_api_datetime(n["updated_at"])).days > days
    ]


class Operation(BaseOperation):
    """Удалить отказы и/или старые отклики. Опционально так же удаляет чаты и блокирует работодателей. Из-за особенностей API эту команду иногда нужно вызывать больше одного раза."""

    __aliases__ = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-b",
            "--blacklist-discard",
            "--blacklist",
            action=argparse.BooleanOptionalAction,
            help="Блокировать работодателя за отказ",
        )
        parser.add_argument(
            "-o",
            "--older-than",
            type=int,
            help="Удаляет любые отклики старше N дней",
        )
        parser.add_argument(
            "--hide-stale",
            type=int,
            metavar="ДНЕЙ",
            help="Скрыть отклики без реакции работодателя старше N дней. Отклик не отзывается, работодатель ничего не получает",
        )
        parser.add_argument(
            "-d",
            "--delete-chat",
            action="store_true",
            help="Удалить так же чат",
        )
        # parser.add_argument(
        #     "--block-ats", action="store_true", help="Блокировать ATS"
        # )
        parser.add_argument(
            "-n",
            "--dry-run",
            action="store_true",
            help="Тестовый запуск без реального удаления",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        self.tool = tool
        self.args = args
        if args.hide_stale:
            self.hide_stale(args.hide_stale)
        else:
            self.clear()

    def hide_stale(self, days: int) -> None:
        """Убирает из списка тихие отклики, не трогая сам отклик."""
        for n in stale_responses(self.tool.get_negotiations(), days):
            vacancy = n["vacancy"]
            if not self.args.dry_run:
                self.delete_chat(n["id"])
            print(
                "🙈 Скрыли тихий отклик" if not self.args.dry_run else "dry-run: скрыли бы",
                vacancy.get("alternate_url"),
                vacancy.get("name"),
            )

    # Эти все функции нужно пособрать и перенести в HHApplicantTool
    def delete_chat(self, topic: int | str) -> bool:
        """Чат можно удалить только через веб-версию"""
        headers = {
            "X-Hhtmfrom": "main",
            "X-Hhtmsource": "negotiation_list",
        }

        payload = {
            "topic": topic,
            "query": "?hhtmFrom=main&hhtmFromLabel=header",
            "substate": "HIDE",
        }

        r = self.tool.xhr_request(
            "POST",
            "/applicant/negotiations/trash",
            payload,
            additional_headers=headers,
            referer_url=f"{self.tool.base_url}/applicant/negotiations?hhtmFrom=main&hhtmFromLabel=header",
        )

        logger.debug(r)

    def clear(self) -> None:
        blacklisted = set(self.tool.get_blacklisted())
        for negotiation in self.tool.get_negotiations():
            vacancy = negotiation["vacancy"]

            if self.args.older_than:
                updated_at = parse_api_datetime(negotiation["updated_at"])
                # А хз какую временную зону сайт возвращает
                days_passed = (
                    dt.datetime.now(updated_at.tzinfo) - updated_at
                ).days
                logger.debug(f"{days_passed = }")
                if days_passed <= self.args.older_than:
                    continue
            elif negotiation["state"]["id"] != "discard":
                continue

            try:
                logger.debug(
                    "Пробуем отменить отклик на %s", vacancy["alternate_url"]
                )

                if not self.args.dry_run:
                    self.tool.api_client.delete(
                        f"/negotiations/active/{negotiation['id']}",
                        with_decline_message=negotiation["state"]["id"]
                        != "discard",
                    )

                    print(
                        "❌ Отменили отклик на вакансию:",
                        vacancy["alternate_url"],
                        vacancy["name"],
                    )

                if self.args.delete_chat:
                    logger.debug(
                        "Пробуем удалить чат с откликом на вакансию %s",
                        vacancy["alternate_url"],
                    )

                    if not self.args.dry_run:
                        if self.delete_chat(negotiation["id"]):
                            print(f"❌ Удалили чат #{negotiation['id']}")

                d = parse_api_datetime(
                    negotiation["updated_at"]
                ) - parse_api_datetime(negotiation["created_at"])

                logger.debug("Ответ на отклик пришел через %d сек.", d.seconds)

                if d.seconds <= 16 * 60:
                    logger.info(
                        "Обьнаружены признаки использования ATS компанией: %s (%s)",
                        vacancy["employer"]["name"],
                        vacancy["employer"]["alternate_url"],
                    )

                employer = vacancy.get("employer", {})
                employer_id = employer.get("id")

                if (
                    self.args.blacklist_discard
                    and employer
                    and employer_id
                    and employer_id not in blacklisted
                ):
                    logger.debug(
                        "Пробуем заблокировать работодателя %s %s",
                        employer["alternate_url"],
                        employer["name"],
                    )

                    if not self.args.dry_run:
                        self.tool.api_client.put(
                            f"/employers/blacklisted/{employer_id}"
                        )
                        blacklisted.add(employer_id)

                        print(
                            "💀 Работодатель заблокирован:",
                            employer["alternate_url"],
                            employer["name"],
                        )
            except ApiError as err:
                logger.error(err)

        print("✅ Удаление откликов завершено.")
