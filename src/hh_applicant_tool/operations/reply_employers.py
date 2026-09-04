from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from ..api import ApiError, datatypes
from ..main import BaseNamespace, BaseOperation
from ..utils.date import parse_api_datetime
from ..utils.string import rand_text

if TYPE_CHECKING:
    from ..main import HHApplicantTool


try:
    import readline

    readline.add_history("/cancel ")
    readline.add_history("/ban")
    readline.set_history_length(10_000)
except ImportError:
    pass


logger = logging.getLogger(__package__)

_PID_FILENAME = "reply-employers.pid"


class Namespace(BaseNamespace):
    reply_message: str
    max_pages: int
    only_invitations: bool
    dry_run: bool
    use_ai: bool
    system_prompt: str
    message_prompt: str
    period: int
    delete_discarded: bool
    delete_blacklisted: bool
    delete_archived: bool
    cleanup: bool
    watch: bool
    interval: int
    daemon: bool
    stop: bool


class Operation(BaseOperation):
    """Ответ всем работодателям."""

    __aliases__ = ["reply-empls", "reply-chats", "reall"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--resume-id",
            help="Идентификатор резюме. Если не указан, то просматриваем чаты для всех резюме",
        )
        parser.add_argument(
            "-m",
            "--reply-message",
            "--reply",
            help="Отправить сообщение во все чаты. Если не передать сообщение, то нужно будет вводить его в интерактивном режиме.",  # noqa: E501
        )
        parser.add_argument(
            "--period",
            type=int,
            help="Игнорировать отклики, которые не обновлялись больше N дней",
        )
        parser.add_argument(
            "-p",
            "--max-pages",
            type=int,
            default=25,
            help="Максимальное количество страниц для проверки",
        )
        parser.add_argument(
            "-oi",
            "--only-invitations",
            help="Отвечать только на приглашения",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--dry-run",
            "--dry",
            help="Не отправлять сообщения, а только выводить параметры запроса",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--delete-discarded",
            "--delete-rejected",
            help="Отменять отклик и удалять чат, если работодатель отказал (state=discard)",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--delete-blacklisted",
            "--delete-blocked",
            help="Отменять отклик и удалять чат с работодателями из чёрного списка",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--delete-archived",
            help="Удалять чат, если вакансия ушла в архив (неактуальна)",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--cleanup",
            "--delete-irrelevant",
            action="store_true",
            default=False,
            help="Почистить все неактуальные чаты: отказы + архивные вакансии + "
            "чёрный список (включает --delete-discarded/-archived/-blacklisted).",
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для автоматической генерации ответов",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="Системный промпт для AI",
            default=(
                "Ты — соискатель, отвечаешь работодателю в чате HeadHunter на его сообщение. "
                "Текст уйдёт работодателю как есть, без редактуры.\n\n"
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:\n"
                "— Эмодзи и любые символы-смайлы: 🤔 😎 🚀 ✅ 👋 ⚡ и подобные. Ни одного.\n"
                "— Восклицательные знаки. Только точки и запятые.\n"
                "— Приветствия «Привет», «Здравствуйте», «Хай», «Добрый день», «Уважаемый HR» (если в начале переписки — допустимо одно нейтральное «Здравствуйте»).\n"
                "— Самопрезентации именем: «X здесь», «Меня зовут X», «X на связи», «Привет, я X». Имя из контекста — это справочные данные для тебя, в текст не вставляй.\n"
                "— Любые упоминания себя в третьем лице («Байназар обсудит», «у Байназара есть опыт»). Пиши ТОЛЬКО от первого лица: «я обсужу», «у меня есть опыт».\n"
                "— Подписи «С уважением», «С наилучшими пожеланиями», «Жду ответа», «Жду обратной связи».\n"
                "— Молодёжно-расслабленные обороты: «прям», «зацепило», «не смог удержаться», «крутая», «классный».\n"
                "— Канцелярит: «выражаю заинтересованность», «имею честь», «являюсь», «обладаю», «осуществляю».\n"
                "— AI-штампы: «ключевой», «значительный опыт», «глубокие знания», «активный участник», «динамично развивающаяся», «реальную пользу», «новые вызовы».\n"
                "— Реверансы: «с удовольствием», «благодарю за внимание», «буду рад возможности обсудить», «надеюсь на сотрудничество».\n"
                "— Деепричастные обороты на старте предложений («являясь...», «обладая...», «имея...»).\n"
                "— Длинное тире. Только обычные точки, запятые, двоеточия.\n"
                "— Квадратные скобки и плейсхолдеры вида [имя]/[компания].\n"
                "— Самохвалебные общие фразы «готов влиться в команду», «достаточно знаний и опыта».\n\n"
                "КАК ОТВЕЧАТЬ:\n"
                "— Сначала прочитай последнее сообщение работодателя и ответь именно на него — не уводи разговор в сторону.\n"
                "— Длина: 1-3 коротких предложения. Если работодатель задал конкретный вопрос — отвечай по делу.\n"
                "— Если работодатель пригласил/предложил созвон/просит контакты — соглашайся, дай нужные данные.\n"
                "— Тон: спокойный, уверенный, профессиональный взрослый разработчик. Не «парень в чате», не пафосный, не угодливый.\n"
                "— Пиши от первого лица. На «вы».\n"
                "— Опирайся на конкретику из переписки и из контекста о кандидате."
            ),
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            help="Промпт для генерации сообщения",
            default="Ответь на последнее сообщение работодателя на основе истории переписки ниже.",
        )
        parser.add_argument(
            "--watch",
            "--loop",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Live-режим: бесконечно опрашивать чаты с паузой --interval. "
            "Остановка по Ctrl+C.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=300,
            help="Пауза между прогонами в секундах для --watch / --daemon (по умолчанию 300).",
        )
        parser.add_argument(
            "--daemon",
            "-D",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Запустить в фоновом режиме: отключиться от терминала и опрашивать чаты "
            "постоянно. Логи пишутся в файл. Остановить: --stop.",
        )
        parser.add_argument(
            "--stop",
            action="store_true",
            default=False,
            help="Остановить фоновый демон reply-employers (отправляет SIGTERM).",
        )

    # ------------------------------------------------------------------
    # Daemon helpers
    # ------------------------------------------------------------------

    def _pid_file(self, tool: "HHApplicantTool") -> Path:
        return tool.config_path / _PID_FILENAME

    def _stop_daemon(self, tool: "HHApplicantTool") -> None:
        pid_file = self._pid_file(tool)
        if not pid_file.exists():
            print("Демон не запущен (PID файл не найден).")
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
        """Unix double-fork: отключает процесс от терминала.

        Родитель печатает подсказку и завершается; дочерний процесс
        (демон) возвращается и продолжает работу в фоне.
        """
        pid_file = self._pid_file(tool)

        if pid_file.exists():
            try:
                existing_pid = int(pid_file.read_text().strip())
                os.kill(existing_pid, 0)
                print(f"Демон уже запущен (PID {existing_pid}).")
                print("Остановить: hh-applicant-tool reply-employers --stop")
                sys.exit(1)
            except ProcessLookupError:
                pid_file.unlink(missing_ok=True)

        log_path = tool.log_file

        try:
            pid = os.fork()
        except OSError as exc:
            sys.exit(f"fork #1 завершился с ошибкой: {exc}")

        if pid > 0:
            # Родитель: печатаем инструкцию и выходим.
            print("Демон reply-employers запущен в фоне.")
            print(f"Логи: {log_path}")
            print("Остановить: hh-applicant-tool reply-employers --stop")
            sys.stdout.flush()
            sys.exit(0)

        os.setsid()

        try:
            pid = os.fork()
        except OSError as exc:
            sys.exit(f"fork #2 завершился с ошибкой: {exc}")

        if pid > 0:
            sys.exit(0)

        # Мы — демон (внук оригинального процесса).
        os.chdir("/")
        pid_file.write_text(str(os.getpid()))

        # Перенаправляем стандартные потоки в лог-файл.
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

        # Переоткрываем Python-объекты потоков, чтобы print() тоже шёл в лог.
        sys.stdin = open(os.devnull, "r")
        sys.stdout = open(log_str, "a", buffering=1)
        sys.stderr = open(log_str, "a", buffering=1)

        def _on_term(signum: int, frame: object) -> None:
            logger.info("Демон получил сигнал %d, завершаю работу.", signum)
            pid_file.unlink(missing_ok=True)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, tool: "HHApplicantTool", args: Namespace) -> None:
        if args.stop:
            self._stop_daemon(tool)
            return

        # Демонизация должна происходить ДО инициализации API-клиента,
        # чтобы форк не захватил открытые TCP-соединения.
        if args.daemon:
            self._daemonize(tool)
            # Только дочерний процесс (демон) продолжает выполнение.

        self.tool = tool
        self.api_client = tool.api_client
        self.resume_id = tool.first_resume_id()
        self.reply_message = args.reply_message or tool.config.get(
            "reply_message"
        )
        self.max_pages = args.max_pages
        self.dry_run = args.dry_run
        self.only_invitations = args.only_invitations
        # --cleanup — зонтичный флаг: включает все виды чистки сразу.
        self.delete_discarded = args.delete_discarded or args.cleanup
        self.delete_blacklisted = args.delete_blacklisted or args.cleanup
        self.delete_archived = args.delete_archived or args.cleanup

        self.message_prompt = args.message_prompt

        effective_system_prompt = self._build_effective_system_prompt(
            args.system_prompt
        )

        self.cover_letter_ai = (
            tool.get_cover_letter_ai(effective_system_prompt)
            if args.use_ai
            else None
        )
        self.period = args.period

        logger.debug(f"{self.reply_message = }")

        loop_mode = args.watch or args.daemon

        if not loop_mode:
            self.reply_employers()
            return

        interval = max(30, args.interval)

        if args.daemon:
            logger.info(
                "Демон reply-employers запущен (PID %d). Интервал ~%dс.",
                os.getpid(),
                interval,
            )
        else:
            logger.info(
                "Live-режим reply-employers: интервал ~%dс. Ctrl+C для остановки.",
                interval,
            )
            print(
                f"Live-режим: проверяю чаты каждые ~{interval}с. "
                "Останови через Ctrl+C."
            )

        while True:
            try:
                self.reply_employers()
            except (ApiError, requests.exceptions.RequestException) as ex:
                logger.error("Ошибка прогона reply-employers: %s", ex)
            sleep_for = interval * random.uniform(0.8, 1.2)
            logger.debug("Сплю %.0fс до следующего прогона.", sleep_for)
            time.sleep(sleep_for)

    # ------------------------------------------------------------------
    # Business logic (unchanged)
    # ------------------------------------------------------------------

    def _build_effective_system_prompt(self, base_prompt: str) -> str:
        """Подмешивает в system-prompt био и FAQ-ответы из конфига."""

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
                faq_lines: list[str] = []
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
                        "Используй эти данные дословно, если вопрос подходит. "
                        "НЕ пиши плейсхолдеры (ХХХ, YYY, [имя], [компания]).\n\n"
                        + "\n\n".join(faq_lines)
                    )

        if not sections:
            return base_prompt

        logger.info(
            "Контекст кандидата подмешан в system-prompt: блоков=%d.",
            len(sections),
        )
        return (
            base_prompt
            + "\n\n## КОНТЕКСТ О КАНДИДАТЕ\n\n"
            + "\n\n".join(sections)
        )

    def _cleanup_chat(
        self,
        *,
        nid: str | int,
        vacancy: dict,
        reason: str,
        send_decline: bool,
    ) -> None:
        url = vacancy.get("alternate_url") or "(no url)"
        if self.dry_run:
            logger.info(
                "[dry-run] удалил бы чат %s (%s): %s", nid, reason, url
            )
            return

        try:
            self.api_client.delete(
                f"/negotiations/active/{nid}",
                with_decline_message=send_decline,
            )
        except Exception as ex:
            logger.warning(
                "Не удалось отменить отклик %s (%s): %s", nid, reason, ex
            )
            return

        if self._delete_chat(nid):
            print(f"Удалил чат {nid} ({reason}): {url}")
        else:
            print(
                f"Отменил отклик {nid} ({reason}), но чат удалить не получилось: {url}"
            )

    def _delete_chat(self, topic: str | int) -> bool:
        """Чат можно удалить только через web-эндпоинт (API не умеет)."""
        headers = {
            "X-Hhtmfrom": "main",
            "X-Hhtmsource": "negotiation_list",
            "X-Requested-With": "XMLHttpRequest",
            "X-Xsrftoken": self.tool.xsrf_token,
            "Referer": "https://hh.ru/applicant/negotiations?hhtmFrom=main&hhtmFromLabel=header",
        }
        payload = {
            "topic": topic,
            "query": "?hhtmFrom=main&hhtmFromLabel=header",
            "substate": "HIDE",
        }
        try:
            r = self.tool.session.post(
                "https://hh.ru/applicant/negotiations/trash",
                payload,
                headers=headers,
            )
            r.raise_for_status()
            return True
        except requests.RequestException as ex:
            logger.error("Не удалось удалить чат %s: %s", topic, ex)
            return False

    def reply_employers(self):
        blacklist = set(self.tool.get_blacklisted())
        me: datatypes.User = self.tool.get_me()
        resumes = self.tool.get_resumes()
        resumes = (
            list(filter(lambda x: x["id"] == self.resume_id, resumes))
            if self.resume_id
            else resumes
        )
        resumes = list(
            filter(
                lambda resume: resume["status"]["id"] == "published", resumes
            )
        )
        self._reply_chats(user=me, resumes=resumes, blacklist=blacklist)

    def _reply_chats(
        self,
        user: datatypes.User,
        resumes: list[datatypes.Resume],
        blacklist: set[str],
    ) -> None:
        resume_map = {r["id"]: r for r in resumes}

        base_placeholders = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
        }

        for negotiation in self.tool.get_negotiations():
            try:
                if not (resume := resume_map.get(negotiation["resume"]["id"])):
                    continue

                updated_at = parse_api_datetime(negotiation["updated_at"])

                if (
                    self.period
                    and (datetime.now(updated_at.tzinfo) - updated_at).days
                    > self.period
                ):
                    continue

                state_id = negotiation["state"]["id"]
                nid = negotiation["id"]
                vacancy = negotiation["vacancy"]
                employer = vacancy.get("employer") or {}
                salary = vacancy.get("salary") or {}

                if state_id == "discard":
                    if self.delete_discarded:
                        self._cleanup_chat(
                            nid=nid,
                            vacancy=vacancy,
                            reason="отказ работодателя",
                            send_decline=False,
                        )
                    continue

                # Вакансия ушла в архив — чат неактуален.
                if vacancy.get("archived"):
                    if self.delete_archived:
                        self._cleanup_chat(
                            nid=nid,
                            vacancy=vacancy,
                            reason="вакансия в архиве",
                            send_decline=False,
                        )
                    continue

                if self.only_invitations and not state_id.startswith("inv"):
                    continue

                if employer.get("id") in blacklist:
                    if self.delete_blacklisted:
                        self._cleanup_chat(
                            nid=nid,
                            vacancy=vacancy,
                            reason="чёрный список",
                            send_decline=True,
                        )
                    else:
                        print(
                            "Пропускаем заблокированного работодателя",
                            employer.get("alternate_url"),
                        )
                    continue

                placeholders = {
                    "vacancy_name": vacancy.get("name", ""),
                    "employer_name": employer.get("name", ""),
                    "resume_title": resume.get("title") or "",
                    **base_placeholders,
                }

                logger.debug(
                    "Вакансия %(vacancy_name)s от %(employer_name)s"
                    % placeholders
                )

                page: int = 0
                last_message: datatypes.Message | None = None
                message_history: list[str] = []
                while True:
                    messages_res: datatypes.PaginatedItems[
                        datatypes.Message
                    ] = self.api_client.get(
                        f"/negotiations/{nid}/messages", page=page
                    )
                    if not messages_res["items"]:
                        break

                    last_message = messages_res["items"][-1]
                    for message in messages_res["items"]:
                        if not message.get("text"):
                            continue
                        author = (
                            "Работодатель"
                            if message["author"]["participant_type"]
                            == "employer"
                            else "Я"
                        )
                        message_date = parse_api_datetime(
                            message.get("created_at")
                        ).strftime("%d.%m.%Y %H:%M:%S")

                        message_history.append(
                            f"[ {message_date} ] {author}: {message['text']}"
                        )

                    if page + 1 >= messages_res["pages"]:
                        break
                    page += 1

                if not last_message:
                    continue

                is_employer_message = (
                    last_message["author"]["participant_type"] == "employer"
                )

                if is_employer_message:
                    send_message = ""
                    if self.reply_message:
                        send_message = (
                            rand_text(self.reply_message) % placeholders
                        )
                        logger.debug(f"Template message: {send_message}")
                    elif self.cover_letter_ai:
                        ai_query = (
                            f"Вакансия: {placeholders['vacancy_name']}\n"
                            f"История переписки:\n"
                            + "\n".join(message_history[-10:])
                            + f"\n\nИнструкция: {self.message_prompt}"
                        )
                        send_message = self.cover_letter_ai.complete(ai_query)
                        logger.debug(f"AI message: {send_message}")
                    else:
                        print("Работодатель:", placeholders["employer_name"])
                        print("Вакансия:", placeholders["vacancy_name"])
                        if salary:
                            print(
                                "Зарплата: от",
                                salary.get("from") or salary.get("to") or 0,
                                "до",
                                salary.get("to") or salary.get("from") or 0,
                                salary.get("currency", "RUR"),
                            )

                        print("\nПоследние сообщения чата:")
                        print()
                        for msg in (
                            message_history[-5:]
                            if len(message_history) > 5
                            else message_history
                        ):
                            print(msg)

                        try:
                            print("-" * 40)
                            print("Активное резюме:", resume.get("title") or "")
                            print(
                                "/ban, /cancel необязательное сообщение для отмены"
                            )
                            send_message = input("Ваше сообщение: ").strip()
                        except EOFError:
                            continue

                        if not send_message:
                            print("Пропускаем чат")
                            continue

                        if send_message.startswith("/ban"):
                            self.api_client.put(
                                f"/employers/blacklisted/{employer['id']}"
                            )
                            blacklist.add(employer["id"])
                            print(
                                "Работодатель заблокирован",
                                employer.get("alternate_url"),
                            )
                            continue
                        elif send_message.startswith("/cancel"):
                            _, decline_msg = send_message.split("/cancel", 1)
                            self.api_client.delete(
                                f"/negotiations/active/{nid}",
                                with_decline_message=decline_msg.strip(),
                            )
                            print("Отмена заявки", vacancy["alternate_url"])
                            continue

                    if self.dry_run:
                        logger.debug(
                            "dry-run: отклик на %s: %s",
                            vacancy["alternate_url"],
                            send_message,
                        )
                        continue

                    self.api_client.post(
                        f"/negotiations/{nid}/messages",
                        message=send_message,
                        delay=random.uniform(1, 3),
                    )
                    print(f"Отправлено для {vacancy['alternate_url']}")

            except ApiError as ex:
                logger.error(ex)

        print("Сообщения разосланы!")
