import base64
import logging
import time
from dataclasses import KW_ONLY, dataclass, field
from email.utils import parsedate_to_datetime
from threading import Lock

import requests

from .base import AIError

logger = logging.getLogger(__package__)

DEFAULT_BASE_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicError(AIError):
    pass


@dataclass
class ChatAnthropic:
    """Клиент Anthropic Claude (нативный Messages API).

    Отличия от OpenAI-протокола: заголовки `x-api-key` + `anthropic-version`,
    `system` — отдельное поле (не в messages), обязательный `max_tokens`,
    ответ в `content[0].text`.
    """

    api_key: str

    _: KW_ONLY

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    system_prompt: str | None = None
    timeout: float = 30.0

    max_retries: int = 5

    temperature: float = 0.0
    # Имя поля совпадает с ChatOpenAI/ChatGigaChat, чтобы один ключ конфига
    # работал для всех провайдеров; в payload идёт как max_tokens.
    max_completion_tokens: int = 1000

    rate_limit: int = 40

    session: requests.Session = field(default_factory=requests.Session)

    _previous_request_time: float = field(default=0.0, init=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = Lock()

    def _default_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    @property
    def _min_request_interval(self) -> float:
        return 60.0 / self.rate_limit if self.rate_limit > 0 else 0.0

    def _request(self, payload: dict) -> requests.Response:
        with self._lock:
            if self._previous_request_time > 0:
                delay = (
                    self._min_request_interval
                    - time.monotonic()
                    + self._previous_request_time
                )
                if delay > 0:
                    logger.debug("Wait %.2fs before Anthropic request", delay)
                    time.sleep(delay)
            try:
                return self.session.post(
                    self.base_url,
                    json=payload,
                    headers=self._default_headers(),
                    timeout=self.timeout,
                )
            finally:
                self._previous_request_time = time.monotonic()

    def _get_retry_delay(
        self, response: requests.Response, attempt: int
    ) -> float:
        min_interval = self._min_request_interval or 1.0
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), min_interval)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after).timestamp()
                    return max(retry_at - time.time(), min_interval)
                except (TypeError, ValueError, OverflowError):
                    pass
        return max(min_interval * (attempt + 1), 1.0)

    def _messages(self, payload: dict) -> str:
        """Общий цикл retry/parse для Messages API. Возвращает текст ответа."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request(payload)
            except requests.exceptions.RequestException as ex:
                raise AnthropicError(f"Network error: {ex}") from ex

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise AnthropicError(
                        f"Anthropic {response.status_code} после ретраев"
                    )
                delay = self._get_retry_delay(response, attempt)
                logger.warning(
                    "Anthropic %d, retry in %.2fs",
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue

            if not response.ok:
                body = (response.text or "").strip()[:500]
                raise AnthropicError(
                    f"Anthropic {response.status_code} "
                    f"(model={payload.get('model')!r}): {body or '<empty>'}"
                )

            try:
                data = response.json()
            except ValueError as ex:
                raise AnthropicError(f"Invalid JSON response: {ex}") from ex

            if data.get("type") == "error":
                err = data.get("error") or {}
                raise AnthropicError(str(err.get("message") or err))

            try:
                # content — список блоков; берём первый text-блок.
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        return block.get("text") or ""
                return ""
            except (KeyError, IndexError, AttributeError) as ex:
                raise AnthropicError(f"Invalid response format: {ex}") from ex

        raise AnthropicError("Anthropic request failed after retries")

    def complete(self, message: str) -> str:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("AI запрос: %s", message)

        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_completion_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": message}],
        }
        if self.system_prompt:
            payload["system"] = self.system_prompt
        return self._messages(payload)

    def solve_captcha(self, image_data: bytes) -> str:
        """Распознаёт текст с капчи через Claude Vision."""
        image_b64 = base64.b64encode(image_data).decode("utf-8")
        payload = {
            "model": self.model,
            "max_tokens": 20,
            "temperature": 0.0,
            "system": (
                "Распознай текст на изображении. Верни ТОЛЬКО текст, "
                "без пояснений, кавычек и пробелов вокруг."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Распознай текст на изображении.",
                        },
                    ],
                }
            ],
        }
        logger.debug("Anthropic captcha request: %d bytes", len(image_data))
        text = self._messages(payload).strip()
        logger.debug("Anthropic captcha распознала: %s", text)
        return text
