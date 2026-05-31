# HH Applicant Tool

CLI-утилита для автоматизации работы на [hh.ru](https://hh.ru):

- Массовая рассылка откликов с AI-сгенерированными сопроводительными письмами.
- Автоматические ответы работодателям в чате.
- AI-фильтрация вакансий перед откликом.
- Автоматическое решение капчи через Vision-модели.
- Поддержка нескольких профилей (аккаунтов) и резюме.
- Поддерживаемые LLM: **GigaChat** (Sber) и любой OpenAI-совместимый API (OpenAI, OpenRouter, Ollama и др.).

[![Publish to PyPI](https://github.com/s3rgeym/hh-applicant-tool/actions/workflows/publish.yml/badge.svg)](https://github.com/s3rgeym/hh-applicant-tool/actions)
[![PyPi Version](https://img.shields.io/pypi/v/hh-applicant-tool)](https://pypi.org/project/hh-applicant-tool/)
[![Python Versions](https://img.shields.io/pypi/pyversions/hh-applicant-tool.svg)](https://pypi.org/project/hh-applicant-tool/)

---

## Содержание

- [Требования](#требования)
- [Установка](#установка)
- [Первый запуск](#первый-запуск)
- [AI-провайдеры](#ai-провайдеры)
  - [GigaChat: один аккаунт](#gigachat-один-аккаунт)
  - [GigaChat: пул аккаунтов с failover](#gigachat-пул-аккаунтов-с-failover)
  - [OpenAI и совместимые API](#openai-и-совместимые-api)
  - [Модели для разных задач](#модели-для-разных-задач)
- [Персонализация писем](#персонализация-писем)
  - [Био из резюме](#био-из-резюме)
  - [Контакты и FAQ](#контакты-и-faq)
  - [Тонкая настройка тона](#тонкая-настройка-тона)
- [Команды](#команды)
  - [apply-vacancies](#apply-vacancies)
  - [reply-employers](#reply-employers)
  - [gigachat-accounts](#gigachat-accounts)
  - [config](#config)
  - [Сервисные команды](#сервисные-команды)
- [Решение капчи](#решение-капчи)
- [Профили (несколько аккаунтов hh.ru)](#профили-несколько-аккаунтов-hhru)
- [Где хранятся данные](#где-хранятся-данные)
- [Запросы к локальной БД](#запросы-к-локальной-бд)
- [Запуск через Docker](#запуск-через-docker)
- [Использование в Python-скриптах](#использование-в-python-скриптах)
- [Лицензия](#лицензия)

---

## Требования

- Python **3.11** или новее.
- macOS, Linux или Windows (включая WSL).
- Аккаунт на hh.ru с **опубликованным** резюме.
- Для AI — либо GigaChat (`GIGACHAT_CREDENTIALS`), либо OpenAI-совместимый API-ключ.

---

## Установка

### Вариант 1: pipx (для повседневного использования)

```sh
pipx install 'hh-applicant-tool[playwright]'
playwright install chromium
```

Extra `playwright` нужен для авторизации (headless Chromium эмулирует
вход через мобильное приложение и обходит ограничения публичного API).

### Вариант 2: Poetry (для разработки и локальной установки)

```sh
git clone https://github.com/s3rgeym/hh-applicant-tool.git
cd hh-applicant-tool
poetry install -E playwright
poetry run playwright install chromium

# Активировать окружение
poetry shell
# или вызывать всё через poetry run:
poetry run hh-applicant-tool --help
```

### Вариант 3: Docker

См. раздел [Запуск через Docker](#запуск-через-docker).

---

## Первый запуск

```sh
# 1. Авторизация на hh.ru (headless-браузер, токены сохранятся в config.json)
hh-applicant-tool authorize

# 2. Подключить GigaChat (см. дальше — есть варианты с одним и несколькими аккаунтами)
hh-applicant-tool gigachat-accounts add \
    --credentials 'MDE5ZD...' \
    --label main \
    --scope GIGACHAT_API_PERS

hh-applicant-tool config --set openai_cover_letter.provider gigachat
hh-applicant-tool config --set openai_cover_letter.model GigaChat-2

# 3. Сухой прогон, чтобы посмотреть, что бы оно отправило
hh-applicant-tool -vv apply-vacancies \
    --use-ai --force-message \
    --search "Python разработчик" \
    --per-page 3 --total-pages 1 \
    --dry-run

# 4. Боевой запуск (без --dry-run, дефолтных лимитов хватает)
hh-applicant-tool -vv apply-vacancies --use-ai --search "Python разработчик"
```

---

## AI-провайдеры

Утилита поддерживает два провайдера: **GigaChat** (Sber, OAuth2) и любой
**OpenAI-совместимый** API (OpenAI, OpenRouter, Ollama и т.п.). Провайдер
выбирается полем `provider` внутри секции конфига:

- `openai_cover_letter` — для генерации сопроводительных писем.
- `openai_vacancy_filter` — для AI-фильтрации вакансий.
- `openai_captcha` — для распознавания капчи.

Названия секций сохранены `openai_*` ради обратной совместимости.

### GigaChat: один аккаунт

Получи Credentials Key в личном кабинете
[GigaChat Studio](https://developers.sber.ru/studio/), затем:

```sh
hh-applicant-tool config --set openai_cover_letter.provider gigachat
hh-applicant-tool config --set openai_cover_letter.credentials 'MDE5ZDE3...=='
hh-applicant-tool config --set openai_cover_letter.scope GIGACHAT_API_PERS
hh-applicant-tool config --set openai_cover_letter.model GigaChat-2
hh-applicant-tool config --set openai_cover_letter.temperature 0.3
```

Доступные scope: `GIGACHAT_API_PERS` (физлица), `GIGACHAT_API_B2B`,
`GIGACHAT_API_CORP`.

### GigaChat: пул аккаунтов с failover

Если хочется не упереться в дневной лимит одного ключа — добавь несколько
аккаунтов. При `GigaChatError` от одного клиент автоматически переключается
на следующий.

```sh
hh-applicant-tool gigachat-accounts add --credentials 'MDE5...' --label main
hh-applicant-tool gigachat-accounts add --credentials 'OTI3...' --label backup

hh-applicant-tool gigachat-accounts list
# GigaChat-аккаунтов: 2
#   [0] main      scope=GIGACHAT_API_PERS  creds=MDE5ZD…0NzQ=
#   [1] backup    scope=GIGACHAT_API_PERS  creds=OTI3M…WUyZQ==

hh-applicant-tool gigachat-accounts remove --index 1
hh-applicant-tool gigachat-accounts clear  # снести все
```

Аккаунты из пула переиспользуются всеми секциями (письма, фильтр, капча).
Если пул пуст, утилита фолбэчится на `credentials` в самой секции.

В логах при использовании пула:

```
[I] GigaChat pool (openai_cover_letter): 2 аккаунтов — main, backup
...
[W] GigaChat complete: аккаунт main (1/2) упал: ... — пробую следующий
[I] GigaChat complete: успешно с аккаунтом backup (2/2)
```

### OpenAI и совместимые API

```sh
hh-applicant-tool config --set openai_cover_letter.provider openai
hh-applicant-tool config --set openai_cover_letter.api_key 'sk-...'
hh-applicant-tool config --set openai_cover_letter.base_url 'https://api.openai.com/v1/chat/completions'
hh-applicant-tool config --set openai_cover_letter.model 'gpt-4o-mini'
```

Для OpenRouter — `base_url=https://openrouter.ai/api/v1/chat/completions`, для
локальной Ollama — `base_url=http://localhost:11434/v1/chat/completions`.

Можно использовать отдельный прокси только для AI-трафика:

```sh
hh-applicant-tool --ai-proxy 'http://user:pass@proxy:8080' apply-vacancies --use-ai
# Или сохранить в конфиг:
hh-applicant-tool config --set openai_cover_letter.proxy_url 'http://user:pass@proxy:8080'
```

### Модели для разных задач

Для писем хватает базовой модели, для капчи нужна Vision (Pro/Max):

```sh
hh-applicant-tool config --set openai_cover_letter.model    GigaChat-2
hh-applicant-tool config --set openai_vacancy_filter.model  GigaChat-2
hh-applicant-tool config --set openai_captcha.model         GigaChat-2-Pro
```

Vision-модели GigaChat: `GigaChat-Pro`, `GigaChat-Max`, `GigaChat-2-Pro`,
`GigaChat-2-Max`. На скоупе `GIGACHAT_API_PERS` `Max` иногда недоступна —
тогда подойдёт `Pro`. Если упадёт `422 Model not found` — переключись на
другую модель той же линейки.

---

## Персонализация писем

По умолчанию письмо собирается из:

- Системного промпта (тон, запреты, формат).
- Контекста вакансии (название, работодатель, описание из `/vacancies/{id}` или snippet, ключевые навыки).
- Био кандидата из конфига (если задано).
- AI-сгенерированного тела письма.
- Блока FAQ + контактов + closing (если задано).

### Био из резюме

Подмешивается в system-prompt — GigaChat ссылается на твои конкретные
проекты и метрики вместо общих фраз. Открой конфиг:

```sh
hh-applicant-tool config --edit
```

Добавь секцию `cover_letter.bio`:

```json
"cover_letter": {
  "bio": "Имя: Иван Иванов. Москва. Golang-разработчик с 3+ годами опыта.\n\nОпыт:\n\n1. Компания X — Golang Developer, июнь 2024 — август 2025.\n   — Развивал рекомендательную систему: метрика +35%, регистрации +11%.\n   — Запускал игровые механики: WAU до 8000.\n   Стек: Go (Fiber, gRPC), PostgreSQL, Kafka, Kubernetes.\n\nОбразование: ..."
}
```

Когда `bio` задан, секции `Меня зовут:` и `Мой опыт:` из per-vacancy
промпта автоматически убираются — модель не получает дублирующий
контекст. Сэкономишь токены.

### Контакты и FAQ

В конец каждого письма автоматически дописывается блок с контактами и
часто задаваемыми вопросами:

```json
"contacts": {
  "email": "you@example.com",
  "telegram": "username",
  "phone": "89001234567",
  "max": "https://max.ru/u/...",
  "faq": [
    { "q": "Зачем ушёл с предыдущего места?", "a": "Сократили команду..." },
    { "q": "Когда можешь выйти?", "a": "Хоть на следующий день." },
    { "q": "Вилка?", "a": "Минимум 250К gross, комфорт — 300К gross." }
  ],
  "closing": "Если ещё остались вопросы — буду рад пообщаться. Хорошего рабочего дня!"
}
```

Можно отключить, удалив секцию или установив `contacts.mode = "off"`.

### Тонкая настройка тона

Дефолтный system-prompt уже включает жёсткие запреты на эмодзи,
канцелярит, штампы («ключевой опыт», «выражаю заинтересованность»),
обращения «Уважаемый HR» и подписи «С уважением». Если нужно своё —
передавай через `--system-prompt "..."` при запуске или сохрани в конфиг.

Температура — отдельный важный параметр. **0.3-0.5** — оптимум: модель
следует инструкциям и при этом не плодит однообразие. Выше 0.7 — модель
начинает игнорировать запреты (вылезают эмодзи, восклицательные знаки).

```sh
hh-applicant-tool config --set openai_cover_letter.temperature 0.3
```

---

## Команды

Полная справка по любой команде:

```sh
hh-applicant-tool --help
hh-applicant-tool <command> --help
```

### `apply-vacancies`

Главная команда — рассылка откликов.

```sh
hh-applicant-tool apply-vacancies [OPTIONS]
```

Полезные флаги:

| Флаг | Назначение |
|---|---|
| `--use-ai` | AI генерирует сопроводительное письмо |
| `--force-message` | Слать письмо для каждой вакансии (а не только когда обязательно) |
| `--ai-filter light\|heavy` | AI отсеивает неподходящие вакансии (см. ниже) |
| `--system-prompt "..."` | Свой системный промпт |
| `--message-prompt "..."` | Свой пользовательский промпт |
| `--search "..."` | Поисковый запрос |
| `--professional-role 96` | Роль (см. `/professional_roles` в HH API) |
| `--experience between3And6` | Опыт |
| `--salary 250000 --currency RUR --only-with-salary` | Минимальная зарплата |
| `--exclude-words "1С" "PHP"` | Стоп-слова в названии |
| `--per-page 100` | Результатов на странице (дефолт 100) |
| `--total-pages 20` | Сколько страниц перебрать (дефолт 20) |
| `--resume-id <id>` | Только для конкретного резюме |
| `--dry-run` | Не отправлять, только показать в логе |
| `-vv` | DEBUG-логи (`-v` — INFO) |

#### Режимы AI-фильтрации

- `--ai-filter light` — быстрая оценка по названию и `key_skills`. Дёшево по токенам.
- `--ai-filter heavy` — полный анализ: должность, навыки, опыт работы из резюме vs описание вакансии. Точнее, но дороже.

Отклонённые вакансии сохраняются в `vacancies_skipped` локальной БД с причиной — можно потом посмотреть через `query`.

### `reply-employers`

Ответы работодателям в чате через AI.

```sh
hh-applicant-tool reply-employers --use-ai [OPTIONS]
```

Утилита отвечает **только** когда последнее сообщение в чате — от
работодателя. Свои сообщения не дублирует, в пустоту не пишет.

| Флаг | Назначение |
|---|---|
| `--use-ai` | AI генерирует ответ из контекста переписки |
| `--only-invitations` | Только приглашения (state начинается с `inv`) |
| `--period 7` | Игнорировать переписки старше N дней |
| `--resume-id <id>` | Только для конкретного резюме |
| `-m "текст"` | Статический ответ вместо AI |
| `--dry-run` | Не отправлять |

### `gigachat-accounts`

Управление пулом GigaChat-аккаунтов. Алиасы: `giga-accounts`, `ga`.

```sh
hh-applicant-tool gigachat-accounts add \
    --credentials 'MDE5...' \
    --label main \
    --scope GIGACHAT_API_PERS

hh-applicant-tool gigachat-accounts list
hh-applicant-tool gigachat-accounts remove --index 0
hh-applicant-tool gigachat-accounts clear
```

### `config`

```sh
hh-applicant-tool config                            # показать весь конфиг
hh-applicant-tool config --key openai_cover_letter  # показать один ключ
hh-applicant-tool config --set foo.bar value        # установить
hh-applicant-tool config --unset foo.bar            # удалить
hh-applicant-tool config --edit                     # открыть в редакторе
hh-applicant-tool config --show-path                # вывести путь к config.json
```

### Сервисные команды

| Команда | Что делает |
|---|---|
| `authorize` | Авторизация через headless Chromium |
| `logout` | Удалить токены текущего профиля |
| `whoami` | Информация о текущем пользователе |
| `list-resumes` | Список резюме с ID |
| `update-resumes` | Обновить дату публикации (поднять в выдаче) |
| `clone-resume --resume-id <id>` | Дубликат резюме |
| `create-resume` | Создать новое резюме |
| `clear-negotiations` | Удалить все отклики (опасно) |
| `clear-skipped` | Очистить базу пропущенных вакансий |
| `refresh-token` | Принудительно обновить access-token |
| `query "SELECT ..."` | SQL-запрос к локальной БД |
| `log` | Показать лог |
| `ui` | Запустить веб-интерфейс |
| `test-session` | Проверить сессию HH |
| `check-proxy` | Проверить прокси |
| `call-api METHOD PATH` | Произвольный вызов HH API |
| `migrate-db` | Миграции локальной БД |
| `settings` | Показать настройки приложения |

---

## Решение капчи

Если hh.ru возвращает капчу (на отклике, запросе вакансии или просмотре
переписок), утилита её автоматически решает:

1. Открывает `captcha_url` в headless Chromium.
2. Делает скриншот изображения капчи.
3. Отправляет в Vision-модель (`openai_captcha`).
4. Подставляет распознанный текст в форму и нажимает Enter.
5. Копирует полученные куки в общую сессию — следующие запросы идут без капчи.

Для GigaChat понадобится Vision-капчевая модель:

```sh
hh-applicant-tool config --set openai_captcha.provider gigachat
hh-applicant-tool config --set openai_captcha.model GigaChat-2-Pro
```

Аккаунты при этом берутся из общего пула `gigachat-accounts`.

Если модель Pro/Max недоступна на твоём скоупе, оставь OpenAI-совместимый
Vision (например, `gpt-4o-mini`) для `openai_captcha`.

---

## Профили (несколько аккаунтов hh.ru)

Каждый профиль — отдельная папка в каталоге приложения с собственными
`config.json`, БД и куки.

```sh
hh-applicant-tool --profile main authorize
hh-applicant-tool --profile work authorize

hh-applicant-tool --profile work apply-vacancies --use-ai
```

По умолчанию используется профиль `.` (без подкаталога).

Через переменную окружения: `HH_PROFILE_ID=work hh-applicant-tool ...`.

---

## Где хранятся данные

| ОС | Корневой каталог |
|---|---|
| macOS | `~/Library/Application Support/hh-applicant-tool/` |
| Linux | `~/.config/hh-applicant-tool/` |
| Windows | `%APPDATA%\hh-applicant-tool\` |

Внутри:

| Файл | Что |
|---|---|
| `config.json` | Главный конфиг (токены, AI-настройки, контакты, FAQ, bio) |
| `cookies.txt` | Куки в Mozilla-формате |
| `hh.sqlite` | Локальная БД (отклики, контакты, пропущенные вакансии) |
| `hh-applicant-tool.log` | Лог приложения |

Переопределить корень: `--config-dir <path>` или `CONFIG_DIR=<path>`.

---

## Запросы к локальной БД

```sh
# Контакты последних 10 работодателей
hh-applicant-tool query \
    "SELECT employer_name, email, phone, vacancy_name
     FROM vacancy_contacts
     ORDER BY id DESC LIMIT 10"

# Сколько вакансий отклонил AI и почему
hh-applicant-tool query \
    "SELECT skip_reason, COUNT(*) as n
     FROM vacancies_skipped
     GROUP BY skip_reason
     ORDER BY n DESC"
```

Схему БД смотри в `src/hh_applicant_tool/storage/`.

---

## Запуск через Docker

```sh
# Авторизация (один раз)
docker-compose run -u docker -it hh_applicant_tool authorize

# Рассылка
docker-compose run -u docker -it hh_applicant_tool \
    apply-vacancies --use-ai --search "Python разработчик"

# Ответы работодателям
docker-compose run -u docker -it hh_applicant_tool \
    reply-employers --use-ai --only-invitations
```

Контейнер монтирует каталог данных `~/.config/hh-applicant-tool` хостовой
машины, так что токены и БД сохраняются между запусками. Параметры — см.
`docker-compose.yml`.

---

## Использование в Python-скриптах

```python
from hh_applicant_tool.api import ApiClient

client = ApiClient(
    client_id="...",
    client_secret="...",
    access_token="...",
    refresh_token="...",
)

# Список резюме
resumes = client.get("/resumes/mine")["items"]

# Поиск вакансий
vacancies = client.get(
    "/vacancies",
    text="Python разработчик",
    per_page=20,
)
```

GigaChat-клиент тоже доступен напрямую:

```python
import requests
from hh_applicant_tool.ai import ChatGigaChat

session = requests.Session()
session.verify = False  # Sber использует российские корневые сертификаты

client = ChatGigaChat(
    credentials="MDE5...",
    scope="GIGACHAT_API_PERS",
    model="GigaChat-2-Pro",
    session=session,
)

answer = client.complete("Привет, как дела?")
print(answer)

# Распознавание текста с изображения (Vision)
with open("captcha.png", "rb") as f:
    text = client.solve_captcha(f.read())
```

---

## Лицензия

Limited Non-Commercial License. См. файл `LICENSE`.

Бесплатно для личного использования. Коммерческое использование —
включая интеграцию в платные сервисы или перепродажу — запрещено.

Канал и чат проекта: [@hh_applicant_tool](https://t.me/hh_applicant_tool),
[@hh_applicant_chat](https://t.me/hh_applicant_chat).
