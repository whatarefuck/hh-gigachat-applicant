from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..utils.notifications import send_telegram_message

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    message: str


class Operation(BaseOperation):
    """Отправить тестовое сообщение в Telegram."""

    __aliases__ = ["test-notify"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--message",
            "-m",
            default="Тестовое сообщение от hh-applicant-tool",
            help="Текст сообщения",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        if send_telegram_message(tool.config, args.message):
            print("✅ Сообщение отправлено. Проверь Telegram.")
        else:
            print(
                "❌ Не отправлено. Проверь notify.telegram.bot_token и "
                "notify.telegram.chat_id:\n"
                "  hh-applicant-tool config --set notify.telegram.bot_token 1234:AA...\n"
                "  hh-applicant-tool config --set notify.telegram.chat_id 123456789"
            )
