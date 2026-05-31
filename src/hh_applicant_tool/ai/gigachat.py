import logging
import time
import uuid
from dataclasses import KW_ONLY, dataclass, field
from email.utils import parsedate_to_datetime
from threading import Lock

import requests

from .base import AIError

logger = logging.getLogger(__package__)

DEFAULT_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
DEFAULT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat"

TOKEN_REFRESH_MARGIN = 60.0


class GigaChatError(AIError):
    pass


@dataclass
class ChatGigaChat:
    credentials: str

    _: KW_ONLY

    scope: str = DEFAULT_SCOPE
    base_url: str = DEFAULT_BASE_URL
    auth_url: str = DEFAULT_AUTH_URL
    model: str = DEFAULT_MODEL
    system_prompt: str | None = None
    timeout: float = 15.0

    max_retries: int = 5

    temperature: float = 0.0
    max_completion_tokens: int = 1000

    rate_limit: int = 40

    session: requests.Session = field(default_factory=requests.Session)

    _access_token: str | None = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0.0, init=False, repr=False)
    _previous_request_time: float = field(default=0.0, init=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = Lock()

    @property
    def _min_request_interval(self) -> float:
        return 60.0 / self.rate_limit if self.rate_limit > 0 else 0.0

    def _ensure_token(self) -> None:
        """Получает или обновляет access_token, если истёк."""
        if (
            self._access_token
            and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN
        ):
            return

        headers = {
            "Authorization": f"Basic {self.credentials}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            response = self.session.post(
                self.auth_url,
                headers=headers,
                data={"scope": self.scope},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as ex:
            raise GigaChatError(f"GigaChat auth failed: {ex}") from ex

        if not response.ok:
            # Подсовываем тело ответа в ошибку — Sber возвращает понятную
            # причину (client.unauthorized / scope.invalid / etc.).
            body = (response.text or "").strip()[:500]
            raise GigaChatError(
                f"GigaChat auth {response.status_code} "
                f"(scope={self.scope}): {body or '<empty body>'}"
            )

        try:
            data = response.json()
        except ValueError as ex:
            raise GigaChatError(f"GigaChat auth: invalid JSON: {ex}") from ex

        token = data.get("access_token")
        expires_at_ms = data.get("expires_at")
        if not token or not expires_at_ms:
            raise GigaChatError(
                f"GigaChat auth: malformed response: {data!r}"
            )

        self._access_token = token
        self._token_expires_at = float(expires_at_ms) / 1000.0
        logger.debug(
            "GigaChat token refreshed, expires at %s",
            time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self._token_expires_at)
            ),
        )

    def _default_headers(self) -> dict[str, str]:
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
        }

    def _request(self, payload: dict) -> requests.Response:
        """Выполнение запроса с минимальным интервалом между запросами."""
        with self._lock:
            if self._previous_request_time > 0:
                delay = (
                    self._min_request_interval
                    - time.monotonic()
                    + self._previous_request_time
                )
                if delay > 0:
                    logger.debug("Wait %.2fs before GigaChat request", delay)
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
        """Вычисление задержки перед повторным запросом при 429 ошибке."""
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

    def complete(self, message: str) -> str:
        """Генерация текста через GigaChat API."""
        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": message})

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("AI запрос: %s", message)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_completion_tokens,
            "stream": False,
        }
        return self._chat_completion(payload)

    def _chat_completion(self, payload: dict) -> str:
        """Общий цикл retry/auth для chat-completions: 401-refresh + 429-backoff.

        Возвращает text из `choices[0].message.content`. На любых сбоях —
        `GigaChatError`.
        """
        token_refreshed = False
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request(payload)
            except requests.exceptions.RequestException as ex:
                raise GigaChatError(f"Network error: {ex}") from ex

            if response.status_code == 401 and not token_refreshed:
                logger.debug(
                    "GigaChat returned 401, refreshing token and retrying"
                )
                self._access_token = None
                self._token_expires_at = 0.0
                token_refreshed = True
                continue

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise GigaChatError("GigaChat rate limit exceeded")
                delay = self._get_retry_delay(response, attempt)
                logger.warning(
                    "GigaChat returned 429 Too Many Requests, retry in %.2fs",
                    delay,
                )
                time.sleep(delay)
                continue

            if not response.ok:
                # Подсовываем тело ответа в ошибку — Sber обычно даёт
                # понятную причину (например, "Model GigaChat-2 not found").
                body = (response.text or "").strip()[:500]
                raise GigaChatError(
                    f"GigaChat chat-completion {response.status_code} "
                    f"(model={payload.get('model')!r}): "
                    f"{body or '<empty body>'}"
                )

            try:
                data = response.json()
            except ValueError as ex:
                raise GigaChatError(f"Invalid JSON response: {ex}") from ex

            if "error" in data:
                error = data["error"]
                message_text = (
                    error.get("message") if isinstance(error, dict) else error
                )
                raise GigaChatError(str(message_text))

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as ex:
                raise GigaChatError(f"Invalid response format: {ex}") from ex
            return content if content is not None else ""

        raise GigaChatError("GigaChat request failed after retries")

    @property
    def _files_url(self) -> str:
        """Эндпоинт /api/v1/files выводится из base_url (chat/completions)."""
        return self.base_url.rsplit("/chat/completions", 1)[0] + "/files"

    def _upload_image(self, image_data: bytes) -> str:
        """Загружает картинку в GigaChat Files API и возвращает file_id."""
        self._ensure_token()
        try:
            response = self.session.post(
                self._files_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                files={"file": ("captcha.png", image_data, "image/png")},
                data={"purpose": "general"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as ex:
            raise GigaChatError(f"GigaChat file upload failed: {ex}") from ex
        except ValueError as ex:
            raise GigaChatError(
                f"GigaChat file upload: invalid JSON: {ex}"
            ) from ex

        file_id = payload.get("id")
        if not file_id:
            raise GigaChatError(
                f"GigaChat file upload: response без id: {payload!r}"
            )
        logger.debug("GigaChat file uploaded: id=%s", file_id)
        return file_id

    def _delete_file(self, file_id: str) -> None:
        """Лучшее-усилием удаляет файл из GigaChat — занимает место в личном кабинете."""
        try:
            self._ensure_token()
            self.session.post(
                f"{self._files_url}/{file_id}/delete",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=self.timeout,
            )
        except Exception as ex:
            logger.debug(
                "GigaChat file %s delete failed (best-effort): %s",
                file_id,
                ex,
            )

    def solve_captcha(self, image_data: bytes) -> str:
        """Распознаёт текст с картинки капчи через GigaChat Vision.

        Требуется vision-capable модель: GigaChat-Pro, GigaChat-Max,
        GigaChat-2-Pro, GigaChat-2-Max. Базовый GigaChat вернёт ошибку.
        """
        file_id = self._upload_image(image_data)
        try:
            return self._solve_captcha_with_file(file_id)
        finally:
            self._delete_file(file_id)

    def _solve_captcha_with_file(self, file_id: str) -> str:
        system_prompt = (
            "Ты распознаёшь текст с изображения капчи. "
            "Верни ТОЛЬКО распознанный текст, без объяснений, кавычек и пробелов вокруг."
        )
        user_text = "Распознай текст на изображении. Верни только результат."

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_text,
                    "attachments": [file_id],
                },
            ],
            "temperature": 0.0,
            "max_tokens": 20,
            "stream": False,
        }

        logger.debug(
            "GigaChat captcha request, file_id=%s, model=%s",
            file_id,
            self.model,
        )
        text = self._chat_completion(payload).strip()
        logger.debug("GigaChat captcha распознала: %s", text)
        return text
