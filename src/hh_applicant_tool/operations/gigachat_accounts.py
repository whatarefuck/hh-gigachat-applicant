from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    action: str
    credentials: str | None
    scope: str | None
    label: str | None
    index: int | None


class Operation(BaseOperation):
    """Управление списком GigaChat-аккаунтов (пул для failover).

    Хранится в `gigachat.accounts` в config.json. При сбое одного аккаунта
    `GigaChatPool` пробует следующий.
    """

    __aliases__ = ["giga-accounts", "ga"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "action",
            choices=["add", "list", "remove", "clear"],
            help="Действие над списком аккаунтов",
        )
        parser.add_argument(
            "--credentials",
            "-c",
            help="GIGACHAT_CREDENTIALS (base64(client_id:client_secret)). Обязательно для 'add'.",
        )
        parser.add_argument(
            "--scope",
            "-s",
            default="GIGACHAT_API_PERS",
            help="Scope (GIGACHAT_API_PERS / _B2B / _CORP). По умолчанию PERS.",
        )
        parser.add_argument(
            "--label",
            "-l",
            help="Человекочитаемая метка аккаунта (например, 'main', 'backup').",
        )
        parser.add_argument(
            "--index",
            "-i",
            type=int,
            help="Индекс аккаунта в списке (0-based). Обязателен для 'remove'.",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        gigachat_cfg = tool.config.setdefault("gigachat", {})
        if not isinstance(gigachat_cfg, dict):
            raise ValueError(
                "config.gigachat не словарь — проверь config.json"
            )
        accounts = gigachat_cfg.setdefault("accounts", [])
        if not isinstance(accounts, list):
            raise ValueError(
                "config.gigachat.accounts не список — проверь config.json"
            )

        if args.action == "add":
            self._add(tool, accounts, args)
        elif args.action == "list":
            self._list(accounts)
        elif args.action == "remove":
            self._remove(tool, accounts, args)
        elif args.action == "clear":
            self._clear(tool, gigachat_cfg)

    @staticmethod
    def _mask(creds: str) -> str:
        creds = creds or ""
        if len(creds) <= 12:
            return "*" * len(creds)
        return creds[:6] + "…" + creds[-4:]

    def _add(
        self,
        tool: HHApplicantTool,
        accounts: list,
        args: Namespace,
    ) -> None:
        if not args.credentials:
            raise ValueError(
                "Нужен --credentials. Пример:\n"
                "  hh-applicant-tool gigachat-accounts add "
                "--credentials 'MDE5...' --scope GIGACHAT_API_PERS --label main"
            )

        for existing in accounts:
            if (
                isinstance(existing, dict)
                and existing.get("credentials") == args.credentials
            ):
                logger.warning(
                    "Аккаунт с такими credentials уже есть (label=%r) — пропускаю",
                    existing.get("label"),
                )
                return

        entry: dict = {"credentials": args.credentials}
        if args.label:
            entry["label"] = args.label
        if args.scope and args.scope != "GIGACHAT_API_PERS":
            entry["scope"] = args.scope
        else:
            entry["scope"] = args.scope or "GIGACHAT_API_PERS"

        accounts.append(entry)
        tool.config.save()
        print(
            f"✅ Аккаунт добавлен: label={entry.get('label', '-')}, "
            f"scope={entry['scope']}, credentials={self._mask(args.credentials)}. "
            f"Всего аккаунтов: {len(accounts)}."
        )

    def _list(self, accounts: list) -> None:
        if not accounts:
            print(
                "Пул пуст. Добавь через:\n"
                "  hh-applicant-tool gigachat-accounts add --credentials '...' --label main"
            )
            return
        print(f"GigaChat-аккаунтов: {len(accounts)}")
        for i, acc in enumerate(accounts):
            if not isinstance(acc, dict):
                print(f"  [{i}] (некорректная запись) {acc!r}")
                continue
            label = acc.get("label") or "(без метки)"
            scope = acc.get("scope") or "GIGACHAT_API_PERS"
            creds = self._mask(acc.get("credentials") or "")
            print(f"  [{i}] {label:20s}  scope={scope:22s}  creds={creds}")

    def _remove(
        self,
        tool: HHApplicantTool,
        accounts: list,
        args: Namespace,
    ) -> None:
        if args.index is None:
            raise ValueError(
                "Нужен --index N (узнай через `gigachat-accounts list`)."
            )
        if not 0 <= args.index < len(accounts):
            raise ValueError(
                f"Индекс {args.index} вне диапазона [0, {len(accounts) - 1}]"
            )

        removed = accounts.pop(args.index)
        tool.config.save()
        label = (
            removed.get("label") if isinstance(removed, dict) else "(unknown)"
        )
        print(
            f"🗑  Удалён аккаунт #{args.index} (label={label}). "
            f"Остаётся: {len(accounts)}."
        )

    def _clear(
        self, tool: HHApplicantTool, gigachat_cfg: dict
    ) -> None:
        count = len(gigachat_cfg.get("accounts") or [])
        gigachat_cfg["accounts"] = []
        tool.config.save()
        print(f"🗑  Очищены все {count} аккаунтов GigaChat.")
