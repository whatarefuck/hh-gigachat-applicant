from __future__ import annotations

import argparse
import asyncio
import logging
from typing import TYPE_CHECKING

from ..habr import HabrCareerClient
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

HABR_BROWSER_DIRNAME = "habr_browser"


class Namespace(BaseNamespace):
    timeout: int


class Operation(BaseOperation):
    """Вход в career.habr.com (открывает браузер, сессия сохраняется в профиле)."""

    __aliases__ = ["habr-auth"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--timeout",
            type=int,
            default=300,
            help="Сколько секунд ждать ручного входа (по умолчанию 300).",
        )

    def run(self, tool: "HHApplicantTool", args: Namespace) -> None:
        user_data_dir = tool.config_path / HABR_BROWSER_DIRNAME
        proxy_url = tool._get_openai_proxies().get("https") or tool._get_proxies().get(
            "https"
        )

        async def _login() -> None:
            async with HabrCareerClient(
                user_data_dir=user_data_dir,
                headless=False,  # интерактивный вход
                proxy_url=proxy_url,
            ) as client:
                await client.ensure_login(login_timeout_sec=args.timeout)

        asyncio.run(_login())
        print("Готово. Теперь можно запускать: hh-applicant-tool habr-apply")
