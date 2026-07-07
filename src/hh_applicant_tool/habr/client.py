"""Клиент для career.habr.com на базе Playwright.

Habr Career — это server-rendered Rails/SPA-сайт БЕЗ публичного API. Отклик на
вакансию — форма с CSRF-токеном внутри SPA-компонента. Поэтому вместо реверса
HTTP-запросов мы управляем настоящим браузером через Playwright: он сам держит
сессию (cookie `_career_session`), CSRF и JS-рендеринг.

Сессия хранится в **persistent context** (каталог браузера в профиле утилиты),
поэтому после первого ручного логина куки переживают перезапуски — отдельно
«сохранять токен» не нужно.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__package__)

BASE_URL = "https://career.habr.com"
VACANCY_ID_RE = re.compile(r"^/vacancies/(\d+)$")


class HabrError(Exception):
    pass


@dataclass
class HabrVacancy:
    id: str
    title: str = ""
    company: str = ""
    url: str = ""
    description: str = ""
    already_applied: bool = False


@dataclass
class HabrCareerClient:
    """Управляет браузером career.habr.com.

    Все методы асинхронные (Playwright async API). Вызывающий код запускает их
    через `asyncio.run(...)` в каждом цикле демона.
    """

    user_data_dir: Path
    headless: bool = True
    proxy_url: str | None = None
    slow_mo_ms: int = 0
    timeout_ms: int = 30000
    # "chrome" — реальный Google Chrome (менее детектируем анти-ботом Habr);
    # None — bundled Chromium (фолбэк, если Chrome не установлен).
    channel: str | None = "chrome"

    _pw: object = field(default=None, init=False, repr=False)
    _context: object = field(default=None, init=False, repr=False)

    # Реалистичный UA настоящего Chrome на macOS.
    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    # Скрипт, прячущий признаки автоматизации (navigator.webdriver и пр.).
    _STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = window.chrome || { runtime: {} };
    """

    async def __aenter__(self) -> "HabrCareerClient":
        from playwright.async_api import async_playwright

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()

        launch_kwargs: dict = {
            "user_data_dir": str(self.user_data_dir),
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
            "viewport": {"width": 1440, "height": 900},
            "locale": "ru-RU",
            "user_agent": self._USER_AGENT,
            # Убираем баннер и флаги автоматизации, которые палят Playwright.
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        }
        if self.proxy_url:
            launch_kwargs["proxy"] = {"server": self.proxy_url}

        # Пробуем реальный Chrome; если не установлен — bundled Chromium.
        try:
            self._context = await self._pw.chromium.launch_persistent_context(
                channel=self.channel, **launch_kwargs
            )
            if self.channel:
                logger.debug("Habr: запущен Google Chrome (channel=%s).", self.channel)
        except Exception as ex:
            logger.warning(
                "Habr: не удалось запустить Chrome (%s), падаю на Chromium. "
                "Анти-бот Habr может детектить Chromium сильнее.",
                ex,
            )
            self._context = await self._pw.chromium.launch_persistent_context(
                **launch_kwargs
            )

        await self._context.add_init_script(self._STEALTH_JS)
        self._context.set_default_timeout(self.timeout_ms)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

    async def _page(self):
        pages = self._context.pages
        if pages:
            return pages[0]
        return await self._context.new_page()

    # ------------------------------------------------------------------
    # Авторизация
    # ------------------------------------------------------------------

    async def is_logged_in(self) -> bool:
        page = await self._page()
        await page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        return await self._check_logged_in(page)

    @staticmethod
    async def _check_logged_in(page) -> bool:
        # Залогинен, если есть личное меню/диалоги и нет ссылки на вход.
        # Устойчиво к навигации: если контекст уничтожен редиректом (частое
        # дело сразу после входа), считаем «пока не готово» и вернём False —
        # опрос попробует снова на следующем шаге.
        try:
            await page.wait_for_load_state(
                "domcontentloaded", timeout=5000
            )
            return await page.evaluate(
                """() => {
                    const hasSignIn = !!document.querySelector('a[href*="sign_in"], a[href*="/users/auth"]');
                    const hasPersonal = !!document.querySelector('a[href="/conversations"], a[href="/profile/notifications"]');
                    return hasPersonal && !hasSignIn;
                }"""
            )
        except Exception as ex:  # noqa: BLE001
            logger.debug("Habr: проверка логина отложена (навигация): %s", ex)
            return False

    async def ensure_login(self, login_timeout_sec: int = 300) -> None:
        """Проверяет сессию. Если не залогинен — открывает окно и ждёт, пока
        пользователь войдёт вручную (в т.ч. решит капчу/2FA).

        Требует НЕ headless режима, иначе войти невозможно.
        """
        if await self.is_logged_in():
            logger.debug("Habr: сессия активна.")
            return

        if self.headless:
            raise HabrError(
                "Нет активной сессии career.habr.com. Запусти сначала "
                "`hh-applicant-tool habr-login` (откроется браузер для входа)."
            )

        page = await self._page()
        await page.goto(f"{BASE_URL}/users/sign_in", wait_until="domcontentloaded")
        print(
            "🔑 Войди в career.habr.com в открывшемся окне. "
            f"Жду до {login_timeout_sec} сек..."
        )
        deadline = login_timeout_sec
        step = 3
        while deadline > 0:
            await asyncio.sleep(step)
            deadline -= step
            if await self._check_logged_in(page):
                print("✅ Вход выполнен, сессия сохранена.")
                logger.info("Habr: вход выполнен, куки сохранены в профиле.")
                return
        raise HabrError("Не дождались входа в career.habr.com.")

    # ------------------------------------------------------------------
    # Поиск вакансий
    # ------------------------------------------------------------------

    async def fetch_vacancy_ids(
        self,
        *,
        search: str = "",
        max_pages: int = 3,
        only_suitable: bool = False,
    ) -> list[str]:
        """Возвращает ID вакансий из поиска career.habr.com.

        - `search` — текстовый запрос (параметр `q`), например «Python разработчик».
        - `only_suitable=True` — раздел «Подходящие» (`type=suitable`, подбор по
          профилю). Обычно даёт уже-откликнутые/нерелевантные — по умолчанию off.
        Сортировка по дате: свежие сверху.
        """
        from urllib.parse import quote

        page = await self._page()
        vac_type = "suitable" if only_suitable else "all"
        q_param = f"&q={quote(search)}" if search else ""
        ids: list[str] = []
        seen: set[str] = set()

        for page_num in range(1, max_pages + 1):
            url = (
                f"{BASE_URL}/vacancies"
                f"?type={vac_type}&sort=date{q_param}&page={page_num}"
            )
            await page.goto(url, wait_until="domcontentloaded")
            page_ids = await page.evaluate(
                """() => {
                    const out = [];
                    for (const a of document.querySelectorAll('a[href^="/vacancies/"]')) {
                        const m = a.getAttribute('href').match(/^\\/vacancies\\/(\\d+)$/);
                        if (m) out.push(m[1]);
                    }
                    return out;
                }"""
            )
            new_ids = [i for i in page_ids if i not in seen]
            if not new_ids:
                break
            for i in new_ids:
                seen.add(i)
                ids.append(i)
            await self._human_pause()

        logger.debug(
            "Habr: найдено %d вакансий (q=%r, type=%s).",
            len(ids),
            search,
            vac_type,
        )
        return ids

    async def fetch_vacancy_details(self, vacancy_id: str) -> HabrVacancy:
        page = await self._page()
        url = f"{BASE_URL}/vacancies/{vacancy_id}"
        await page.goto(url, wait_until="domcontentloaded")

        data = await page.evaluate(
            """() => {
                const title = document.querySelector('h1')?.textContent?.trim() || '';
                const company = document.querySelector('a[href^="/companies/"]')?.textContent?.trim() || '';
                const body = document.querySelector('.vacancy-description, .basic-section, main')?.textContent?.trim() || document.body.textContent.trim();
                // Уже откликнулись: на странице есть блок с отправленным откликом
                // (.vacancy-response), кнопки Редактировать/Удалить или ссылка
                // «Посмотреть отклик». Textarea для нового отклика при этом нет.
                const applied = !!document.querySelector('.vacancy-response, .create-vacancy-response__controls')
                    || [...document.querySelectorAll('a,button')].some(e => /Посмотреть отклик/i.test(e.textContent));
                return { title, company, description: body.slice(0, 4000), applied };
            }"""
        )
        return HabrVacancy(
            id=vacancy_id,
            title=data.get("title", ""),
            company=data.get("company", ""),
            url=url,
            description=data.get("description", ""),
            already_applied=bool(data.get("applied")),
        )

    # ------------------------------------------------------------------
    # Отклик
    # ------------------------------------------------------------------

    async def apply(
        self, vacancy_id: str, message: str, *, dry_run: bool = True
    ) -> bool:
        """Отклик на вакансию. Возвращает True, если отклик отправлен.

        При dry_run=True только заполняет форму, но НЕ нажимает submit.
        """
        page = await self._page()
        url = f"{BASE_URL}/vacancies/{vacancy_id}"
        await page.goto(url, wait_until="domcontentloaded")

        if await self._is_already_applied(page):
            logger.info("Habr: на вакансию %s уже откликались.", vacancy_id)
            return False

        # Поле отклика — textarea в секции «Ваш отклик».
        textarea = page.locator("textarea").last
        try:
            await textarea.wait_for(state="visible", timeout=8000)
        except Exception:
            logger.warning(
                "Habr: не нашёл поле отклика на %s (возможно, отклик по email "
                "или внешняя форма) — пропускаю.",
                vacancy_id,
            )
            return False

        await textarea.fill(message)
        await self._human_pause()

        submit = page.get_by_role("button", name="Откликнуться")
        # На странице две кнопки «Откликнуться»; submit — последняя (в форме).
        submit_btn = submit.last

        if dry_run:
            logger.info(
                "Habr [dry-run]: заполнил отклик на %s (%s), не отправляю.",
                vacancy_id,
                url,
            )
            return False

        await submit_btn.click()
        await asyncio.sleep(random.uniform(1.5, 3.0))

        applied = await self._is_already_applied(page)
        if applied:
            logger.info("Habr: 📨 отклик отправлен на %s (%s).", vacancy_id, url)
        else:
            logger.warning(
                "Habr: после клика по «Откликнуться» подтверждение не найдено "
                "для %s. Возможно, требуется доп. форма/вопросы.",
                vacancy_id,
            )
        return applied

    @staticmethod
    async def _is_already_applied(page) -> bool:
        # Уже откликнулись: есть блок отправленного отклика (.vacancy-response)
        # с кнопками Редактировать/Удалить или ссылка «Посмотреть отклик».
        try:
            return await page.evaluate(
                """() => {
                    if (document.querySelector('.vacancy-response, .create-vacancy-response__controls')) return true;
                    return [...document.querySelectorAll('a,button')].some(e => /Посмотреть отклик/i.test(e.textContent));
                }"""
            )
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Имитация активности
    # ------------------------------------------------------------------

    async def imitate_activity(self) -> None:
        """Имитирует человеческую активность: заходит на случайные разделы,
        скроллит, открывает вакансию. Снижает «ботоподобность» паттерна."""
        page = await self._page()
        routes = [
            f"{BASE_URL}/vacancies?type=all&sort=date",
            f"{BASE_URL}/vacancies?type=suitable",
            f"{BASE_URL}/companies/ratings",
            f"{BASE_URL}/salaries",
            f"{BASE_URL}/",
        ]
        route = random.choice(routes)
        try:
            await page.goto(route, wait_until="domcontentloaded")
            for _ in range(random.randint(2, 5)):
                await page.mouse.wheel(0, random.randint(300, 900))
                await asyncio.sleep(random.uniform(0.6, 2.0))
        except Exception as ex:
            logger.debug("Habr: имитация активности не удалась: %s", ex)

    @staticmethod
    async def _human_pause() -> None:
        await asyncio.sleep(random.uniform(0.8, 2.5))
