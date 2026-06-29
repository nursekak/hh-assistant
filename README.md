<b>English</b> · <a href="#hh-ассистент">Русский</a>

# HH Assistant

**Automated HH.ru job-application system with an LLM pipeline and semantic resume matching.**

Telegram bot + web dashboard: finds matching vacancies, compares them with your resume via embeddings, generates personalized cover letters, and applies — all automatically.

---

## Architecture

Layered architecture with clear separation of concerns: thin entry points (Telegram/Web) → services (business logic) → repositories (data access) → `storage`. The heavy pipeline runs in a dedicated worker process via a Redis queue.

```
   Telegram Bot (aiogram)        Web Dashboard (FastAPI + Alpine.js)
   Cards / Apply / Scan          Settings / Analytics / Resumes / Live
        └──────────────┬───────────────────┬──────────────┘
                       ▼                    ▼
              ┌───────────────────────────────────────┐
              │  Services (business logic)             │
              │  Scan · Apply · Resume · Response ·    │
              │  Settings · Analytics · Dashboard      │
              └───────────────────┬───────────────────┘
                                  ▼
              ┌───────────────────────────────────────┐
              │  Repositories (data access)            │
              │  Vacancy · Resume · ResumeVersion ·    │
              │  ScanJob · Settings · Analytics        │
              └───────────────────┬───────────────────┘
                                  ▼
                          storage.py (aiosqlite)

   Enqueue a scan                       ┌──────────────────────────┐
   bot/web ── enqueue ──► Redis (ARQ) ──►│  Worker (separate process)│
                                         │  scan / responses / reparse│
                                         └─────────────┬──────────────┘
                                                       ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  HH.ru (Playwright stealth)  →  LLM pipeline (Ollama)         │
   │  Search/parse                →  Extractor · Matcher (bge-m3)  │
   │                              →  Cover Letter (Ollama/Claude)  │
   └──────────────────────────────────────────────────────────────┘
```

> If `REDIS_URL` is not set, tasks run inline inside the bot process (graceful fallback) — a separate worker is not required for local runs.

---

## Features

### Search and filtering
- Playwright automation of HH.ru with anti-bot evasion (stealth JS, cookie session, random delays)
- **Full results scrolling**: the page is smoothly scrolled to the very bottom to load **all** lazily-rendered cards (up to 50 per page), not just the visible part
- **Pagination**: the bot pages through search results and collects the configured number of **unique new** vacancies (not seen before), instead of just cards from the first page
- Filters built right into the HH query: region, period, work format (remote / office / hybrid), employment type, minimum salary
- **HH-side experience filter**: the candidate's seniority (from settings or auto-derived from the resume) is mapped to HH categories (`2 years → "1–3 years"`) to filter out fewer suitable vacancies
- Automatic skipping of vacancies with overly high required seniority — **before** the expensive LLM analysis
- **Telegram delivery threshold**: vacancies below the match threshold can be stored only in the DB (without a Telegram card and without an LLM summary)

### Telegram channels (second vacancy source)
- **Telethon user-session**: reads new posts from channels in a **Telegram folder** on your personal account (parallel with HH in one scan job)
- Combined scan via `asyncio.gather`: if one source fails, the other continues; preflight fails only when **both** are unavailable
- Same matching pipeline and threshold; TG cards show Match %, **draft cover letter** (manual copy), and **Open post** — no auto-apply
- Web settings: toggle, folder name, lookback hours, per-channel message limit, session status, **List folders** button
- **In-bot connection**: the `/tg_login` command (or the **"📡 Connect TG"** button) runs an interactive login right inside the chat — phone → code → 2FA password, with the user-session saved automatically
  - To avoid Telegram invalidating a code sent as plain text, the bot asks you to enter the code **with separators** (e.g. `1 2 3 4 5`) and strips everything but digits
  - During login the `TG_USER_LOCK` is held so a background scan never touches the same session file
- Alternative one-time CLI login: `python tg_login.py` (or `docker compose run --rm worker python tg_login.py`)

### Visual scan preview
- A **"Parsing"** section in the dashboard: full-page **screenshots of each search page** + a table of found cards marked 🟢 new / 🟠 already in DB
- **Live mode**: while a scan is running the page refreshes live — you can see the parser walking through pages in real time
- **Completeness diagnostics**: shows how many vacancies **HH reported** vs. the number **parsed** — instantly reveals whether everything was loaded and why so many new ones were found
- Artifacts are stored in the shared volume `data/scan_debug/<job_id>/` (last N runs are kept)

### LLM resume ↔ vacancy matching
- Structured requirement extraction via Ollama (`qwen2.5:7b`) into JSON
- **Hybrid matching**: exact skill intersection + semantic similarity via embeddings (`bge-m3`, NumPy cosine similarity)
- Configurable match threshold; vacancies below it are shown with a warning
- TF-IDF fallback if embeddings are unavailable

### Cover letter generation
- Personalized letters via Ollama or the Claude API
- Signed with the candidate's name (`candidate_name` in settings), no placeholders like "[Your name]"
- Preview, edit, and confirm before sending in Telegram
- Automatic filling of the HH.ru application form

### Resume versioning
- Every change to the resume text is stored as an **immutable version** (`resume_versions`)
- `sha256` deduplication: re-parsing identical text does not spawn a new version
- Version history in the web UI and **rollback** to any previous version in one click
- The `resumes` table stays the current snapshot — matching and letters work unchanged

### Security
- The `sanitizer.py` module detects and neutralizes **prompt injection** in vacancy texts (15+ patterns: jailbreak, DAN, system tags, RU/EN attacks)
- Secrets are never stored in code — only in `.env`

### Web dashboard (port 8080)
- **Dashboard**: live scan monitoring with logs, application stats
- **Settings**: 5 sections (search, matching, AI models, schedule, **Telegram channels**) — applied without restart
- **Resumes**: management, full text and skills view, top missing skills
- **Vacancies**: history with Match %, matched/missing skills, and letters
- **Analytics**: funnel, activity by day, Match % histogram, top missing skills (Chart.js)
- **Parsing**: search-page screenshots and the list of found cards with live updates during a scan

### Background tasks and state
- **ARQ + Redis task queue**: scanning, response checking, and resume re-parsing run in a separate worker process — the heavy Playwright/LLM pipeline doesn't block Telegram
- **Job Manager**: scan state is stored in the DB (`scan_jobs`) with an explicit phase state machine (`queued → searching → matching → finalizing → done/error`), survives restarts, supports retries and timeouts
- **Hybrid lock** (`distributed_lock.py`): browser/session access is serialized between the bot and the worker via a distributed Redis lock (degrades to `asyncio.Lock` without Redis)
- **Graceful fallback**: without `REDIS_URL` tasks run inline inside the bot process

### Infrastructure
- Full containerization (Docker Compose): `bot` + `worker` + `ollama` + `redis` services
- `entrypoint.sh` automatically pulls the LLM and embedding models on startup
- APScheduler with dynamic scan-interval changes without restart
- Unit tests (pytest): sanitizer, extractor, matcher, cookies, scan state, experience filter, resume versioning, pagination, HH filter URL building, TG message mapping

---

## Stack

| Category | Technologies |
|---|---|
| Telegram | `aiogram 3.x`, FSM, `Telethon` (channel scan) |
| Web backend | `FastAPI`, `Jinja2` |
| Web frontend | `Alpine.js`, `Tailwind CSS`, `Chart.js` |
| Automation | `Playwright` (stealth mode) |
| LLM / Embeddings | `Ollama`, `bge-m3`, `qwen2.5` |
| ML / matching | `NumPy`, `scikit-learn` (TF-IDF fallback) |
| Database | `aiosqlite` (SQLite) |
| Task queue | `ARQ`, `Redis` |
| Scheduler | `APScheduler` |
| AI API | `Anthropic Claude API` (optional) |
| Containers | `Docker`, `Docker Compose` |
| Tests | `pytest`, `pytest-asyncio` |

---

## Quick start

### Requirements
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — the only dependency
- A Telegram bot (create one via [@BotFather](https://t.me/botfather))
- An HH.ru account

### 1. Setup

```bash
git clone https://github.com/<username>/hh-assistant.git
cd hh-assistant
cp .env.example .env
```

Fill in `.env`:

```env
TELEGRAM_TOKEN=...         # from @BotFather
ALLOWED_USER_ID=...        # from @userinfobot
OLLAMA_MODEL=qwen2.5:7b    # qwen2.5:3b if low on RAM
CANDIDATE_NAME=Gleb        # name used to sign cover letters
# REDIS_URL is set automatically in docker-compose (redis://redis:6379).
# Leave empty for a local run without a worker — tasks will run inline.
```

### 2. Run

```bash
docker compose up -d --build
```

On the first run Ollama downloads the models (~5–6 GB). This may take 10–30 minutes.

Logs:
```bash
docker compose logs -f bot     # Telegram + web
docker compose logs -f worker  # background tasks (scan, applications)
```

### 3. First steps

1. Open the Telegram bot → `/login` — authorize via phone number
2. `/resumes` — load resumes from HH.ru and pick the active one
3. Web UI: [http://localhost:8080](http://localhost:8080) → Settings → set the search query
4. *(Optional)* Telegram channels: get `API_ID`/`API_HASH` at [my.telegram.org](https://my.telegram.org), add them to `.env`, restart (`docker compose up -d bot worker`), then connect right in the bot via `/tg_login` (or the **"📡 Connect TG"** button) and enable the source in Settings → **Telegram channels**
5. Click "Run scan" or wait for the automatic run

---

## Resource requirements

| Model | RAM | Quality | Speed (CPU) |
|---|---|---|---|
| `qwen2.5:3b` | ~4 GB | Good | ~30 sec/vacancy |
| `qwen2.5:7b` | ~8 GB | Excellent | ~60 sec/vacancy |
| `qwen2.5:14b` | ~16 GB | Superb | ~2 min/vacancy |

In Docker Desktop: **Settings → Resources → Memory** — allocate enough.

---

## Bot commands

Available via the native Telegram menu (the "Menu" button / `/`) and via a persistent keyboard with quick-action buttons (shown after `/start` or `/menu`).

| Command | Description |
|---|---|
| `/start`, `/menu` | Show the menu and the quick-action keyboard |
| `/login` | Log in to HH.ru via SMS |
| `/import` | Import cookies (recommended login method) |
| `/search Python Backend` | Set the search query |
| `/resumes` | Manage resumes |
| `/scan` | Run a scan immediately |
| `/status` | Status and statistics |
| `/threshold 65` | Set the match threshold (%) |
| `/tg_login` | Connect Telegram channels (interactive login: phone → code → 2FA) |

---

## Project structure

```
hh-assistant/
├── bot.py              # Entry point: Telegram + APScheduler + FastAPI
├── worker.py           # ARQ WorkerSettings + tasks (scan/responses/reparse)
├── worker_main.py      # Worker launcher (works around uvloop in the arq CLI)
├── scraper.py          # Playwright automation of HH.ru
├── extractor.py        # LLM profile extraction (JSON) from texts
├── matcher.py          # Hybrid matching (exact + semantic)
├── embeddings.py       # bge-m3 embeddings via the Ollama API
├── experience.py       # Parsing and comparing seniority
├── hh_filters.py       # Building the HH search URL (experience/format/employment)
├── pagination.py       # Page-by-page collection of unique new vacancies
├── letter.py           # Cover letter generation
├── sanitizer.py        # Prompt-injection protection
├── llm.py              # Ollama API (vacancy analysis)
├── storage.py          # aiosqlite: vacancies, resumes, versions, settings, scan_jobs
├── scan_state.py       # /api/scan/status response model (data in scan_jobs)
├── scan_phases.py      # Scan phase state machine
├── scan_debug.py       # Visual debugging: search screenshots + card manifest
├── tg_client.py        # Telethon: Telegram folders + channel messages → VacancyData
├── tg_login.py         # One-time CLI login for user-session (data/tg_user.session)
├── tg_parse.py         # Pure mapping helpers (message → VacancyData, URLs)
├── vacancy_types.py    # Shared VacancyData model (HH, TG, …)
├── distributed_lock.py # Hybrid lock (asyncio + Redis) for browser/session
├── config.py           # Configuration from .env
├── services/           # Business logic (scan, apply, resume, response, …)
│   └── job_queue.py    # ARQ enqueue + inline fallback
├── repositories/       # Data access (vacancy, resume, resume_version, scan_job, …)
├── web/
│   ├── app.py          # FastAPI app and /api/scan/*
│   ├── routers/        # settings, resumes, analytics, scan_debug (Parsing)
│   └── templates/      # Jinja2 + Tailwind
├── tests/              # pytest unit tests
├── Dockerfile
├── docker-compose.yml  # bot + worker + ollama + redis
├── entrypoint.sh       # Auto-pull of Ollama models
└── .env.example
```

---

## Roadmap

Legend: ✅ done · 🔜 planned · 💡 idea under consideration.

### Done ✅
- Layered architecture (services / repositories / storage)
- Background worker on ARQ + Redis, Job Manager with a state machine, hybrid lock
- Resume versioning with rollback
- Personalized cover letters (Ollama / Claude)
- Pagination and collection of **unique new** vacancies
- HH filters by experience / work format / employment type (with seniority mapping)
- Full results scrolling (loading all cards)
- Visual scan preview with screenshots and live mode
- Buttons and a native command menu in Telegram
- **Telegram channels** as a second vacancy source (Telethon, folder scan, combined HH+TG job, in-bot `/tg_login`)

### Multi-service parsing 🔜
The main direction is to make the system **not tied to HH.ru** and parse several platforms (HH, **LinkedIn**, and optionally other job boards).

- **Provider abstraction** (`providers/`): a single `VacancyProvider` interface with `search()`, `fetch_details()`, `apply()`. HH.ru becomes the first implementation (`HHProvider`); the current `scraper.py` moves under this interface without changing the matching/letters pipeline.
- **LinkedIn provider** (`LinkedInProvider`): search and parse LinkedIn vacancies (via a cookie-based Playwright session, like HH). Vacancy fields are normalized into a shared `VacancyData` so matching/letters work the same across platforms.
- **Platform selection in settings**: "HH.ru", "LinkedIn" checkboxes — a scan runs across all enabled providers and merges results into a single feed (with deduplication by company + title across platforms).
- **Provider-specific filters**: a shared settings layer (experience, format, period) is translated into each platform's parameters (`hh_filters.py` → a similar `linkedin_filters.py`).

### "Remote Worldwide" search mode 🔜
A dedicated mode for worldwide remote search with a target number of vacancies:

- A **"Remote Worldwide"** toggle in settings/search: when enabled, providers search specifically for remote vacancies without a region (HH: `schedule=remote` without `area`; LinkedIn: `Remote` + worldwide geo).
- A **"How many vacancies to collect"** field (`N`) — the target number of unique new vacancies; pagination pages through as many results as needed to reach `N` (within the `SCAN_MAX_PAGES` safety cap).
- Out-of-the-box scenario: *"find N vacancies that are remote worldwide"* — with a single toggle, without manually configuring region and format per platform.

### Further 💡
- Extended per-platform analytics (where the best matches come from)
- A queue of application drafts with manual batch confirmation
- Export of found vacancies (CSV / JSON)
- Notifications about new employer replies from different platforms in one stream

---

## License

MIT

---

<a href="#hh-assistant">English</a> · <b>Русский</b>

# HH Ассистент

**Система автоматизации отклика на вакансии HH.ru с LLM-пайплайном и семантическим матчингом резюме.**

Telegram-бот + веб-дашборд: находит подходящие вакансии, сравнивает их с резюме через эмбеддинги, генерирует персонализированные сопроводительные письма и откликается — всё автоматически.

---

## Архитектура

Слоистая архитектура с разделением ответственности: тонкие точки входа (Telegram/Web) → сервисы (бизнес-логика) → репозитории (доступ к данным) → `storage`. Тяжёлый пайплайн вынесен в отдельный процесс-воркер через очередь Redis.

```
   Telegram Bot (aiogram)        Web Dashboard (FastAPI + Alpine.js)
   Карточки / Отклик / Скан      Настройки / Аналитика / Резюме / Live
        └──────────────┬───────────────────┬──────────────┘
                       ▼                    ▼
              ┌───────────────────────────────────────┐
              │  Services (бизнес-логика)              │
              │  Scan · Apply · Resume · Response ·    │
              │  Settings · Analytics · Dashboard      │
              └───────────────────┬───────────────────┘
                                  ▼
              ┌───────────────────────────────────────┐
              │  Repositories (доступ к данным)        │
              │  Vacancy · Resume · ResumeVersion ·    │
              │  ScanJob · Settings · Analytics        │
              └───────────────────┬───────────────────┘
                                  ▼
                          storage.py (aiosqlite)

   Постановка скана в очередь            ┌──────────────────────────┐
   bot/web ── enqueue ──► Redis (ARQ) ──►│  Worker (отдельный процесс)│
                                         │  scan / responses / reparse│
                                         └─────────────┬──────────────┘
                                                       ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  HH.ru (Playwright stealth)  →  LLM-пайплайн (Ollama)         │
   │  Поиск/парсинг               →  Extractor · Matcher (bge-m3)  │
   │                              →  Cover Letter (Ollama/Claude)  │
   └──────────────────────────────────────────────────────────────┘
```

> Если `REDIS_URL` не задан, задачи выполняются inline в процессе бота (graceful fallback) — отдельный воркер не обязателен для локального запуска.

---

## Возможности

### Поиск и фильтрация
- Playwright-автоматизация HH.ru с обходом anti-bot (stealth JS, cookie-сессия, случайные задержки)
- **Полная прокрутка выдачи**: страница пролистывается плавно до конца, чтобы догрузить **все** лениво-рендерящиеся карточки (до 50 на страницу), а не только видимую часть
- **Пагинация**: бот листает страницы поиска и набирает заданное число **уникальных новых** вакансий (не виденных ранее), а не просто карточек с первой страницы
- Фильтры прямо в запросе HH: регион, период, формат работы (удалённо / офис / гибрид), тип занятости, минимальная зарплата
- **Фильтр по опыту на стороне HH**: стаж кандидата (из настроек или автоматически из резюме) маппится в категории HH (`2 года → «1–3 года»`), чтобы меньше отсеивать подходящие вакансии
- Автоматический пропуск вакансий с завышенным требованием стажа — **до** дорогого LLM-анализа
- **Порог отправки в Telegram**: вакансии ниже порога совпадения можно сохранять только в БД (без карточки в Telegram и без LLM-разбора)

### Telegram-каналы (второй источник вакансий)
- **Telethon user-session**: чтение новых постов из каналов в **папке Telegram** личного аккаунта (параллельно с HH в одном scan job)
- Объединённый скан через `asyncio.gather`: при падении одного источника второй продолжает; preflight падает только если **оба** недоступны
- Общий пайплайн матчинга и порог; для TG — карточка с Match %, **черновик письма** (копировать вручную), кнопка **Открыть пост** — без авто-отклика
- Веб-настройки: тумблер, имя папки, глубина (часы), лимит сообщений на канал, статус сессии, кнопка **Показать папки**
- **Подключение прямо из бота**: команда `/tg_login` (или кнопка **«📡 Подключить TG»**) запускает интерактивный вход прямо в чате — телефон → код → пароль 2FA, user-session сохраняется автоматически
  - Чтобы Telegram не аннулировал код, присланный обычным текстом, бот просит вводить код **с разделителями** (напр. `1 2 3 4 5`) и оставляет только цифры
  - На время входа удерживается `TG_USER_LOCK`, чтобы фоновый скан не трогал тот же файл сессии
- Альтернативный одноразовый CLI-логин: `python tg_login.py` (или `docker compose run --rm worker python tg_login.py`)

### Визуальный просмотр парсинга
- Раздел **«Парсинг»** в дашборде: полностраничные **скриншоты каждой страницы поиска** + таблица найденных карточек с пометкой 🟢 новая / 🟠 уже в базе
- **Live-режим**: пока скан идёт, страница обновляется вживую — видно, как парсер проходит страницы в реальном времени
- **Диагностика полноты**: рядом показывается, сколько вакансий **заявил HH** против числа **спарсенных** — сразу видно, всё ли догрузилось и почему нашлось столько новых
- Артефакты складываются в общий volume `data/scan_debug/<job_id>/` (хранятся последние N прогонов)

### LLM-матчинг резюме ↔ вакансия
- Структурированное извлечение требований через Ollama (`qwen2.5:7b`) в JSON-формат
- **Гибридный матчинг**: точное пересечение навыков + семантическое сходство через эмбеддинги (`bge-m3`, NumPy cosine similarity)
- Настраиваемый порог совпадения; вакансии ниже порога показываются с предупреждением
- TF-IDF fallback если эмбеддинги недоступны

### Генерация сопроводительных писем
- Персонализированные письма через Ollama или Claude API
- Подпись именем кандидата (`candidate_name` в настройках) без плейсхолдеров вроде «[Ваше имя]»
- Предпросмотр, редактирование и подтверждение перед отправкой в Telegram
- Автоматическое заполнение формы отклика на HH.ru

### Версионность резюме
- Каждое изменение текста резюме сохраняется как **неизменяемая версия** (`resume_versions`)
- Дедупликация по `sha256`: повторный парсинг идентичного текста новую версию не плодит
- История версий в веб-интерфейсе и **откат** к любой предыдущей версии одним кликом
- Таблица `resumes` остаётся актуальным снапшотом — матчинг и письма работают без изменений

### Безопасность
- Модуль `sanitizer.py` — детектирует и нейтрализует **prompt injection** в текстах вакансий (15+ паттернов: jailbreak, DAN, системные теги, русско/англоязычные атаки)
- Секреты не хранятся в коде — только в `.env`

### Веб-дашборд (порт 8080)
- **Дашборд**: live-мониторинг скана с логами, статистика откликов
- **Настройки**: 5 секций (поиск, матчинг, ИИ-модели, расписание, **Telegram-каналы**) — применяются без рестарта
- **Резюме**: управление, просмотр полного текста и навыков, топ пропущенных скиллов
- **Вакансии**: история с Match %, совпавшими/пропущенными навыками и письмами
- **Аналитика**: воронка, активность по дням, гистограмма Match %, топ пропущенных навыков (Chart.js)
- **Парсинг**: скриншоты страниц поиска и список найденных карточек с live-обновлением во время скана

### Фоновые задачи и состояние
- **Очередь задач на ARQ + Redis**: скан, проверка ответов и перепарсинг резюме выполняет отдельный процесс-воркер — тяжёлый Playwright/LLM-пайплайн не блокирует Telegram
- **Job Manager**: состояние скана хранится в БД (`scan_jobs`) с явной стейт-машиной фаз (`queued → searching → matching → finalizing → done/error`), переживает рестарт, поддерживает retry и таймаут
- **Гибридный лок** (`distributed_lock.py`): доступ к браузеру/сессии сериализуется между ботом и воркером через распределённый Redis-лок (с деградацией до `asyncio.Lock` без Redis)
- **Graceful fallback**: без `REDIS_URL` задачи выполняются inline в процессе бота

### Инфраструктура
- Полная контейнеризация (Docker Compose): сервисы `bot` + `worker` + `ollama` + `redis`
- `entrypoint.sh` автоматически пуллит LLM и embedding-модели при старте
- APScheduler с динамическим изменением интервала скана без рестарта
- Unit-тесты (pytest): sanitizer, extractor, matcher, cookies, scan state, experience filter, версионность резюме, пагинация, построение URL-фильтров HH, **маппинг TG-сообщений**

---

## Стек

| Категория | Технологии |
|---|---|
| Telegram | `aiogram 3.x`, FSM, `Telethon` (скан каналов) |
| Web backend | `FastAPI`, `Jinja2` |
| Web frontend | `Alpine.js`, `Tailwind CSS`, `Chart.js` |
| Автоматизация | `Playwright` (stealth-mode) |
| LLM / Embeddings | `Ollama`, `bge-m3`, `qwen2.5` |
| ML / матчинг | `NumPy`, `scikit-learn` (TF-IDF fallback) |
| База данных | `aiosqlite` (SQLite) |
| Очередь задач | `ARQ`, `Redis` |
| Планировщик | `APScheduler` |
| AI API | `Anthropic Claude API` (опционально) |
| Контейнеры | `Docker`, `Docker Compose` |
| Тесты | `pytest`, `pytest-asyncio` |

---

## Быстрый старт

### Требования
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — единственная зависимость
- Telegram-бот (создать у [@BotFather](https://t.me/botfather))
- Аккаунт HH.ru

### 1. Настройка

```bash
git clone https://github.com/<username>/hh-assistant.git
cd hh-assistant
cp .env.example .env
```

Заполни `.env`:

```env
TELEGRAM_TOKEN=...         # от @BotFather
ALLOWED_USER_ID=...        # от @userinfobot
OLLAMA_MODEL=qwen2.5:7b    # qwen2.5:3b если мало RAM
CANDIDATE_NAME=Глеб        # имя для подписи в сопроводительных письмах
# REDIS_URL задаётся автоматически в docker-compose (redis://redis:6379).
# Оставь пустым для локального запуска без воркера — задачи пойдут inline.
```

### 2. Запуск

```bash
docker compose up -d --build
```

При первом запуске Ollama скачает модели (~5–6 GB). Это может занять 10–30 минут.

Логи:
```bash
docker compose logs -f bot     # Telegram + веб
docker compose logs -f worker  # фоновые задачи (скан, отклики)
```

### 3. Первый запуск

1. Открой Telegram-бота → `/login` — авторизация через номер телефона
2. `/resumes` — загрузи резюме с HH.ru и выбери активное
3. Веб-интерфейс: [http://localhost:8080](http://localhost:8080) → Настройки → задай поисковый запрос
4. *(Опционально)* Telegram-каналы: получи `API_ID`/`API_HASH` на [my.telegram.org](https://my.telegram.org), добавь в `.env`, перезапусти (`docker compose up -d bot worker`), затем подключись прямо в боте через `/tg_login` (или кнопку **«📡 Подключить TG»**) и включи источник в Настройках → **Telegram-каналы**
5. Нажми «Запустить скан» или дождись автоматического запуска

---

## Требования к ресурсам

| Модель | RAM | Качество | Скорость (CPU) |
|---|---|---|---|
| `qwen2.5:3b` | ~4 GB | Хорошее | ~30 сек/вакансия |
| `qwen2.5:7b` | ~8 GB | Отличное | ~60 сек/вакансия |
| `qwen2.5:14b` | ~16 GB | Превосходное | ~2 мин/вакансия |

В Docker Desktop: **Settings → Resources → Memory** — выдели достаточно.

---

## Команды бота

Доступны через нативное меню Telegram (кнопка «Menu» / `/`) и через постоянную клавиатуру с кнопками быстрых действий (показывается после `/start` или `/menu`).

| Команда | Описание |
|---|---|
| `/start`, `/menu` | Показать меню и клавиатуру быстрых действий |
| `/login` | Авторизация на HH.ru по SMS |
| `/import` | Импорт cookies (рекомендуемый способ входа) |
| `/search Python Backend` | Задать поисковый запрос |
| `/resumes` | Управление резюме |
| `/scan` | Запустить скан немедленно |
| `/status` | Статус и статистика |
| `/threshold 65` | Установить порог совпадения (%) |
| `/tg_login` | Подключить Telegram-каналы (интерактивный вход: телефон → код → 2FA) |

---

## Структура проекта

```
hh-assistant/
├── bot.py              # Точка входа: Telegram + APScheduler + FastAPI
├── worker.py           # ARQ WorkerSettings + задачи (scan/responses/reparse)
├── worker_main.py      # Запуск воркера (обход uvloop в arq CLI)
├── scraper.py          # Playwright-автоматизация HH.ru
├── extractor.py        # LLM-извлечение профилей (JSON) из текстов
├── matcher.py          # Гибридный матчинг (точный + семантический)
├── embeddings.py       # bge-m3 эмбеддинги через Ollama API
├── experience.py       # Парсинг и сравнение стажа
├── hh_filters.py       # Построение URL поиска HH (опыт/формат/занятость)
├── pagination.py       # Постраничный сбор уникальных новых вакансий
├── letter.py           # Генерация сопроводительных писем
├── sanitizer.py        # Защита от prompt injection
├── llm.py              # Ollama API (анализ вакансий)
├── storage.py          # aiosqlite: вакансии, резюме, версии, настройки, scan_jobs
├── scan_state.py       # Модель ответа /api/scan/status (данные в scan_jobs)
├── scan_phases.py      # Стейт-машина фаз скана
├── scan_debug.py       # Визуальная отладка: скриншоты поиска + манифест карточек
├── tg_client.py        # Telethon: папки Telegram + сообщения каналов → VacancyData
├── tg_login.py         # Одноразовый CLI-логин user-session (data/tg_user.session)
├── tg_parse.py         # Чистые функции маппинга (сообщение → VacancyData, URL)
├── vacancy_types.py    # Общая модель VacancyData (HH, TG, …)
├── distributed_lock.py # Гибридный лок (asyncio + Redis) для браузера/сессии
├── config.py           # Конфигурация из .env
├── services/           # Бизнес-логика (scan, apply, resume, response, …)
│   └── job_queue.py    # ARQ enqueue + inline fallback
├── repositories/       # Доступ к данным (vacancy, resume, resume_version, scan_job, …)
├── web/
│   ├── app.py          # FastAPI-приложение и /api/scan/*
│   ├── routers/        # settings, resumes, analytics, scan_debug (Парсинг)
│   └── templates/      # Jinja2 + Tailwind
├── tests/              # pytest unit-тесты
├── Dockerfile
├── docker-compose.yml  # bot + worker + ollama + redis
├── entrypoint.sh       # Автозагрузка моделей Ollama
└── .env.example
```

---

## Роадмапа

Условные обозначения: ✅ готово · 🔜 в планах · 💡 идея на проработке.

### Сделано ✅
- Слоистая архитектура (services / repositories / storage)
- Фоновый воркер на ARQ + Redis, Job Manager со стейт-машиной, гибридный лок
- Версионность резюме с откатом
- Персонализированные сопроводительные письма (Ollama / Claude)
- Пагинация и сбор **уникальных новых** вакансий
- Фильтры HH по опыту / формату работы / типу занятости (с маппингом стажа)
- Полная прокрутка выдачи (догрузка всех карточек)
- Визуальный просмотр парсинга со скриншотами и live-режимом
- Кнопки и нативное меню команд в Telegram
- **Telegram-каналы** как второй источник вакансий (Telethon, скан папки, объединённый HH+TG job, вход из бота `/tg_login`)

### Мульти-сервисный парсинг 🔜
Главное направление — сделать систему **не привязанной к HH.ru** и парсить несколько площадок (HH, **LinkedIn**, при желании — другие job-борды).

- **Абстракция провайдера** (`providers/`): единый интерфейс `VacancyProvider` с методами `search()`, `fetch_details()`, `apply()`. HH.ru становится первой реализацией (`HHProvider`), текущий `scraper.py` переезжает под этот интерфейс без изменения пайплайна матчинга/писем.
- **LinkedIn-провайдер** (`LinkedInProvider`): поиск и парсинг вакансий LinkedIn (через Playwright-сессию по cookies, аналогично HH). Нормализация полей вакансии в общий `VacancyData`, чтобы матчинг/письма работали одинаково для всех площадок.
- **Выбор площадок в настройках**: чекбоксы «HH.ru», «LinkedIn» — скан проходит по всем включённым провайдерам и сводит результаты в единую ленту (с дедупликацией по компании+названию между площадками).
- **Провайдер-специфичные фильтры**: общий слой настроек (опыт, формат, период) транслируется в параметры каждой площадки (`hh_filters.py` → аналогичный `linkedin_filters.py`).

### Режим поиска «Remote Worldwide» 🔜
Отдельный режим поиска удалёнки по всему миру с целевым количеством вакансий:

- Тумблер **«Remote Worldwide»** в настройках/поиске: при включении провайдеры ищут именно удалённые вакансии без привязки к региону (HH: `schedule=remote` без `area`; LinkedIn: `Remote` + worldwide-гео).
- Поле **«Сколько вакансий собрать»** (`N`) — целевое число уникальных новых вакансий; пагинация листает столько страниц, сколько нужно, чтобы набрать `N` (в пределах защитного потолка `SCAN_MAX_PAGES`).
- Сценарий из коробки: *«найти N вакансий, которые remote worldwide»* — одним переключателем, без ручной настройки региона и формата под каждую площадку.

### Дальше 💡
- Расширенная аналитика по площадкам (откуда приходят лучшие совпадения)
- Очередь черновиков откликов с ручным подтверждением пачкой
- Экспорт найденных вакансий (CSV / JSON)
- Уведомления о новых ответах работодателей с разных площадок в одном потоке

---

## Лицензия

MIT
