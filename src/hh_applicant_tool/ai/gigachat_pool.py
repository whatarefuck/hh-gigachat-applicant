import logging
from typing import Callable, TypeVar

from .gigachat import ChatGigaChat, GigaChatError

logger = logging.getLogger(__package__)

T = TypeVar("T")


class GigaChatPool:
    """Список `ChatGigaChat`-клиентов с failover.

    На каждый публичный вызов (`complete`, `solve_captcha`) пробует клиентов по
    очереди; при `GigaChatError` от одного — переходит к следующему. Если все
    провалились — поднимает последнюю ошибку.

    Сценарий: несколько GigaChat-аккаунтов с независимыми квотами.
    """

    def __init__(self, clients: list[ChatGigaChat], *, labels: list[str] | None = None) -> None:
        if not clients:
            raise ValueError("GigaChatPool требует хотя бы одного клиента")
        self.clients = clients
        self.labels = labels or [f"#{i + 1}" for i in range(len(clients))]
        if len(self.labels) != len(self.clients):
            raise ValueError("len(labels) должно совпадать с len(clients)")

    def complete(self, message: str) -> str:
        return self._try_each(lambda c: c.complete(message), "complete")

    def solve_captcha(self, image_data: bytes) -> str:
        return self._try_each(
            lambda c: c.solve_captcha(image_data), "solve_captcha"
        )

    def _try_each(self, action: Callable[[ChatGigaChat], T], name: str) -> T:
        last_error: Exception | None = None
        total = len(self.clients)
        for idx, (client, label) in enumerate(
            zip(self.clients, self.labels), 1
        ):
            try:
                result = action(client)
                if idx > 1:
                    logger.info(
                        "GigaChat %s: успешно с аккаунтом %s (%d/%d)",
                        name,
                        label,
                        idx,
                        total,
                    )
                return result
            except GigaChatError as ex:
                last_error = ex
                if idx < total:
                    logger.warning(
                        "GigaChat %s: аккаунт %s (%d/%d) упал: %s — пробую следующий",
                        name,
                        label,
                        idx,
                        total,
                        ex,
                    )
                else:
                    logger.error(
                        "GigaChat %s: последний аккаунт %s (%d/%d) тоже упал: %s",
                        name,
                        label,
                        idx,
                        total,
                        ex,
                    )

        # Все провалились — поднимаем последнюю ошибку
        if last_error is not None:
            raise last_error
        raise GigaChatError(
            f"GigaChatPool.{name}: все {total} аккаунтов провалились"
        )
