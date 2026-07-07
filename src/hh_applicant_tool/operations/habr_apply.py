from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..ai.base import AIError
from ..habr import HabrCareerClient, HabrError
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

HABR_BROWSER_DIRNAME = "habr_browser"
_APPLIED_FILENAME = "habr_applied.json"
_PID_FILENAME = "habr-apply.pid"

DEFAULT_SYSTEM_PROMPT = (
    "Ты пишешь короткий сопроводительный отклик на IT-вакансию на Хабр Карьере "
    "от лица кандидата. Текст уйдёт работодателю как есть.\n\n"
    "ЗАПРЕЩЕНО: эмодзи, восклицательные знаки, самопрезентация именем "
    "(«меня зовут X», «X на связи»), третье лицо о себе, канцелярит "
    "(«выражаю заинтересованность», «являюсь»), AI-штампы («ключевой опыт», "
    "«значительный опыт»), реверансы («благодарю за внимание», «буду рад "
    "возможности обсудить»), подписи «С уважением», плейсхолдеры [имя]/[ХХХ].\n\n"
    "КАК ПИСАТЬ: 2-4 коротких предложения от первого лица. Опирайся на "
    "конкретные технологии из вакансии и мой опыт. Тон — спокойный, "
    "профессиональный. Без общих фраз."
)
DEFAULT_MESSAGE_PROMPT = "Напиши сопроводительный отклик по данным ниже."


class Namespace(BaseNamespace):
    use_ai: bool
    system_prompt: str
    message_prompt: str
    search: str
    only_suitable: bool
    max_pages: int
    dry_run: bool
    watch: bool
    daemon: bool
    stop: bool
    interval: int
    headful: bool
    limit: int


class Operation(BaseOperation):
    """Откликаться на подходящие вакансии career.habr.com (с AI-письмами)."""

    __aliases__ = ["habr", "habr-career"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--use-ai",
            "--ai",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Генерировать отклик через AI (по умолчанию да).",
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            default=DEFAULT_SYSTEM_PROMPT,
            help="Системный промпт для AI.",
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            default=DEFAULT_MESSAGE_PROMPT,
            help="Пользовательский промпт для AI.",
        )
        parser.add_argument(
            "--search",
            "-s",
            default="Python разработчик",
            help="Поисковый запрос по вакансиям (по умолчанию «Python разработчик»). "
            "Пустая строка '' — без поиска, весь список.",
        )
        parser.add_argument(
            "--only-suitable",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Раздел «Подходящие» (type=suitable, подбор по профилю). "
            "По умолчанию off — используется поиск --search.",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=3,
            help="Сколько страниц списка вакансий обрабатывать (по умолчанию 3).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Максимум откликов за один прогон (по умолчанию 20).",
        )
        parser.add_argument(
            "--dry-run",
            "--dry",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="НЕ отправлять отклики, только показывать (ПО УМОЛЧАНИЮ ВКЛ). "
            "Для реальной отправки укажи --no-dry-run.",
        )
        parser.add_argument(
            "--headful",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Показывать окно браузера (по умолчанию headless).",
        )
        parser.add_argument(
            "--watch",
            "--loop",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Бесконечный цикл с паузой --interval. Ctrl+C для остановки.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=3600,
            help="Пауза между прогонами в секундах (по умолчанию 3600 = 1 час).",
        )
        parser.add_argument(
            "--daemon",
            "-D",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Запустить в фоне (отключиться от терминала). Остановить: --stop.",
        )
        parser.add_argument(
            "--stop",
            action="store_true",
            default=False,
            help="Остановить фоновый демон habr-apply.",
        )

    # ------------------------------------------------------------------
    # Daemon helpers (тот же паттерн, что в reply-employers)
    # ------------------------------------------------------------------

    def _pid_file(self, tool: "HHApplicantTool") -> Path:
        return tool.config_path / _PID_FILENAME

    def _stop_daemon(self, tool: "HHApplicantTool") -> None:
        pid_file = self._pid_file(tool)
        if not pid_file.exists():
            print("Демон habr-apply не запущен (PID файл не найден).")
            return
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"SIGTERM отправлен процессу {pid}.")
            pid_file.unlink(missing_ok=True)
        except ProcessLookupError:
            print("Процесс уже завершён.")
            pid_file.unlink(missing_ok=True)
        except Exception as exc:
            logger.error("Не удалось остановить демон: %s", exc)

    def _daemonize(self, tool: "HHApplicantTool") -> None:
        pid_file = self._pid_file(tool)
        if pid_file.exists():
            try:
                existing_pid = int(pid_file.read_text().strip())
                os.kill(existing_pid, 0)
                print(f"Демон уже запущен (PID {existing_pid}).")
                print("Остановить: hh-applicant-tool habr-apply --stop")
                sys.exit(1)
            except ProcessLookupError:
                pid_file.unlink(missing_ok=True)

        log_path = tool.log_file
        try:
            pid = os.fork()
        except OSError as exc:
            sys.exit(f"fork #1 завершился с ошибкой: {exc}")
        if pid > 0:
            print("Демон habr-apply запущен в фоне.")
            print(f"Логи: {log_path}")
            print("Остановить: hh-applicant-tool habr-apply --stop")
            sys.stdout.flush()
            sys.exit(0)

        os.setsid()
        try:
            pid = os.fork()
        except OSError as exc:
            sys.exit(f"fork #2 завершился с ошибкой: {exc}")
        if pid > 0:
            sys.exit(0)

        os.chdir("/")
        pid_file.write_text(str(os.getpid()))
        sys.stdout.flush()
        sys.stderr.flush()

        log_str = str(log_path)
        devnull_fd = os.open(os.devnull, os.O_RDONLY)
        log_fd = os.open(log_str, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(devnull_fd, 0)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(devnull_fd)
        os.close(log_fd)
        sys.stdin = open(os.devnull, "r")
        sys.stdout = open(log_str, "a", buffering=1)
        sys.stderr = open(log_str, "a", buffering=1)

        def _on_term(signum: int, frame: object) -> None:
            logger.info("Демон habr-apply получил сигнал %d, завершаю.", signum)
            pid_file.unlink(missing_ok=True)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, tool: "HHApplicantTool", args: Namespace) -> None:
        if args.stop:
            self._stop_daemon(tool)
            return

        if args.daemon:
            self._daemonize(tool)  # дальше только дочерний процесс

        self.tool = tool
        self.args = args
        self.user_data_dir = tool.config_path / HABR_BROWSER_DIRNAME
        self.applied_path = tool.config_path / _APPLIED_FILENAME
        self.proxy_url = tool._get_openai_proxies().get(
            "https"
        ) or tool._get_proxies().get("https")

        self.cover_letter_ai = (
            tool.get_cover_letter_ai(self._build_system_prompt(args.system_prompt))
            if args.use_ai
            else None
        )

        if args.dry_run:
            logger.info("habr-apply: DRY-RUN — отклики НЕ отправляются.")

        loop_mode = args.watch or args.daemon
        if not loop_mode:
            self._run_cycle()
            return

        interval = max(60, args.interval)
        logger.info("habr-apply: цикл каждые ~%dс (PID %d).", interval, os.getpid())
        if args.watch:
            print(f"Live-режим: проверяю вакансии каждые ~{interval}с. Ctrl+C — стоп.")

        while True:
            try:
                self._run_cycle()
            except HabrError as ex:
                logger.error("habr-apply: %s", ex)
            except Exception as ex:  # noqa: BLE001
                logger.exception("habr-apply: непредвиденная ошибка: %s", ex)
            sleep_for = interval * random.uniform(0.85, 1.15)
            logger.debug("habr-apply: сплю %.0fс.", sleep_for)
            time.sleep(sleep_for)

    # ------------------------------------------------------------------
    # Один прогон
    # ------------------------------------------------------------------

    def _run_cycle(self) -> None:
        asyncio.run(self._run_cycle_async())

    async def _run_cycle_async(self) -> None:
        applied = self._load_applied()
        headless = not self.args.headful

        async with HabrCareerClient(
            user_data_dir=self.user_data_dir,
            headless=headless,
            proxy_url=self.proxy_url,
        ) as client:
            await client.ensure_login()

            ids = await client.fetch_vacancy_ids(
                search=self.args.search,
                max_pages=self.args.max_pages,
                only_suitable=self.args.only_suitable,
            )
            new_ids = [i for i in ids if i not in applied]
            logger.info(
                "habr-apply: всего %d, новых %d.", len(ids), len(new_ids)
            )

            applied_count = 0
            for vacancy_id in new_ids:
                if applied_count >= self.args.limit:
                    logger.info("habr-apply: достигнут лимит %d.", self.args.limit)
                    break

                vacancy = await client.fetch_vacancy_details(vacancy_id)
                if vacancy.already_applied:
                    applied.add(vacancy_id)
                    self._save_applied(applied)
                    continue

                message = self._build_message(vacancy)
                sent = await client.apply(
                    vacancy_id, message, dry_run=self.args.dry_run
                )

                # Помечаем обработанной и в dry-run (чтобы не долбить одну и ту же),
                # и при реальной отправке.
                applied.add(vacancy_id)
                self._save_applied(applied)
                if sent:
                    applied_count += 1
                    print(f"📨 Отклик отправлен: {vacancy.title} — {vacancy.url}")

                await asyncio.sleep(random.uniform(3.0, 8.0))

            # Имитация человеческой активности после откликов.
            await client.imitate_activity()

        logger.info("habr-apply: прогон завершён (отправлено %d).", applied_count)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_message(self, vacancy) -> str:
        if self.cover_letter_ai:
            prompt = (
                f"{self.args.message_prompt}\n\n"
                f"Вакансия: {vacancy.title}\n"
                f"Компания: {vacancy.company}\n\n"
                f"Описание вакансии:\n{vacancy.description}"
            )
            try:
                return self.cover_letter_ai.complete(prompt).strip()
            except AIError:
                logger.exception("habr-apply: AI-ошибка, использую заглушку.")
        # Фолбэк без AI.
        return (
            "Здравствуйте. Заинтересовала ваша вакансия "
            f"«{vacancy.title}». Готов обсудить детали и мой опыт."
        )

    def _build_system_prompt(self, base_prompt: str) -> str:
        def clean(value: object) -> str:
            return str(value).strip() if value is not None else ""

        sections: list[str] = []
        cover_letter_cfg = self.tool.config.get("cover_letter") or {}
        if isinstance(cover_letter_cfg, dict):
            bio = clean(cover_letter_cfg.get("bio"))
            if bio:
                sections.append("### Био\n\n" + bio)

        contacts_cfg = self.tool.config.get("contacts") or {}
        if isinstance(contacts_cfg, dict):
            faq = contacts_cfg.get("faq") or []
            if isinstance(faq, list):
                faq_lines = []
                for entry in faq:
                    if not isinstance(entry, dict):
                        continue
                    q = clean(entry.get("q") or entry.get("question"))
                    a = clean(entry.get("a") or entry.get("answer"))
                    if q and a:
                        faq_lines.append(f"Q: {q}\nA: {a}")
                if faq_lines:
                    sections.append(
                        "### Ответы на типовые вопросы\n\n"
                        "Используй эти данные дословно, если вопрос подходит.\n\n"
                        + "\n\n".join(faq_lines)
                    )

        if not sections:
            return base_prompt
        return base_prompt + "\n\n## КОНТЕКСТ О КАНДИДАТЕ\n\n" + "\n\n".join(sections)

    def _load_applied(self) -> set[str]:
        try:
            data = json.loads(self.applied_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
        except FileNotFoundError:
            pass
        except Exception as ex:  # noqa: BLE001
            logger.warning("habr-apply: не смог прочитать %s: %s", self.applied_path, ex)
        return set()

    def _save_applied(self, applied: set[str]) -> None:
        try:
            self.applied_path.write_text(
                json.dumps(sorted(applied), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("habr-apply: не смог сохранить %s: %s", self.applied_path, ex)
