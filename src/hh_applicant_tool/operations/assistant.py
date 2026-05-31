from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..utils.notifications import send_telegram_message

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)

ASSISTANT_SYSTEM_PROMPT = (
    "Ты — персональный ассистент по поиску работы на hh.ru. Помогаешь кандидату "
    "улучшать отклики и поддерживать актуальное резюме (bio).\n\n"
    "На основе данных ниже (текущее bio, статистика откликов, причины, по которым "
    "AI-фильтр отклонял вакансии):\n"
    "1. Если bio неполное или не покрывает частые требования вакансий — предложи "
    "конкретные дополнения (какие технологии/проекты/факты добавить).\n"
    "2. Задай 1-3 уточняющих вопроса, ответы на которые помогут писать более "
    "точные сопроводительные письма.\n"
    "3. Если данных мало — просто задай вопросы про опыт, стек и пожелания.\n\n"
    "Формат: это сообщение в Telegram. Пиши на русском, по-дружески, кратко "
    "(до 10 предложений), без канцелярита и эмодзи. Обращайся на «ты»."
)


class Namespace(BaseNamespace):
    dry_run: bool
    days: int


class Operation(BaseOperation):
    """AI-ассистент: анализирует отклики и пишет рекомендации в Telegram."""

    __aliases__ = ["advise", "ai-assist"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            "--dry",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Не отправлять в Telegram, вывести в консоль.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="За сколько последних дней брать статистику (по умолчанию 7).",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        context = self._gather_context(tool, days=args.days)

        ai = tool.get_cover_letter_ai(ASSISTANT_SYSTEM_PROMPT)
        logger.debug("assistant prompt:\n%s", context)
        message = ai.complete(context).strip()

        if not message:
            print("AI вернул пустой ответ.")
            return

        if args.dry_run:
            print("=== Сообщение ассистента (dry-run) ===\n")
            print(message)
            return

        if send_telegram_message(tool.config, message):
            print("✅ Рекомендации ассистента отправлены в Telegram.")
        else:
            print(
                "⚠️ Telegram не настроен — вывожу в консоль:\n\n" + message
            )

    def _gather_context(self, tool: HHApplicantTool, *, days: int) -> str:
        parts: list[str] = []

        # 1. Текущее bio
        cover_letter_cfg = tool.config.get("cover_letter") or {}
        bio = ""
        if isinstance(cover_letter_cfg, dict):
            bio = str(cover_letter_cfg.get("bio") or "").strip()
        parts.append(
            "Текущее bio кандидата:\n"
            + (bio if bio else "(не заполнено)")
        )

        # 2. Статистика по пропущенным вакансиям из локальной БД
        try:
            reasons = tool.db.execute(
                "SELECT reason, COUNT(*) AS n FROM skipped_vacancies "
                "WHERE created_at >= datetime('now', ?) "
                "GROUP BY reason ORDER BY n DESC LIMIT 10",
                (f"-{days} days",),
            ).fetchall()
        except Exception as ex:
            logger.debug("Не удалось прочитать skipped_vacancies: %s", ex)
            reasons = []

        if reasons:
            stats = "\n".join(f"  {row[0]}: {row[1]}" for row in reasons)
            parts.append(
                f"Причины пропуска вакансий за последние {days} дней:\n{stats}"
            )

        # 3. Примеры названий отклонённых AI вакансий — показывают, какие
        #    требования встречаются, но кандидат под них не проходит фильтр.
        try:
            sample = tool.db.execute(
                "SELECT name FROM skipped_vacancies "
                "WHERE reason LIKE 'ai_%' AND name IS NOT NULL "
                "AND created_at >= datetime('now', ?) "
                "ORDER BY created_at DESC LIMIT 15",
                (f"-{days} days",),
            ).fetchall()
        except Exception as ex:
            logger.debug("Не удалось прочитать названия вакансий: %s", ex)
            sample = []

        if sample:
            names = "\n".join(f"  - {row[0]}" for row in sample)
            parts.append(
                "Примеры вакансий, которые AI-фильтр счёл неподходящими:\n"
                + names
            )

        return "\n\n".join(parts)
