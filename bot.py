"""
Главный файл бота. Запуск: python bot.py

Команды:
  /start   — приветствие
  /login   — авторизация на HH.ru через OTP
  /search  — задать поисковый запрос (например: /search Python Backend)
  /scan    — ручной запуск сканирования прямо сейчас
  /status  — статистика откликов из базы
  /stop    — остановить бота

Флоу вакансии:
  1. Планировщик запускает scan() каждые N часов
  2. Playwright ищет новые вакансии
  3. sanitizer очищает текст от prompt-injection
  4. Ollama анализирует и строит выжимку
  5. Telegram получает карточку с кнопками [✅ Откликнуться] [❌ Пропустить] [🔗 Открыть]
"""

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import storage
import scraper
import sanitizer
import llm
import matcher
import extractor
import experience
import letter
from extractor import ResumeProfile, VacancyProfile
from scan_state import STATE as scan_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

bot = Bot(token=config.TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

scheduler = AsyncIOScheduler()


# ───────────────────────────── FSM States ──────────────────────────────────

class LoginFlow(StatesGroup):
    waiting_for_phone = State()
    waiting_for_otp   = State()


class ApplyFlow(StatesGroup):
    waiting_for_letter_edit = State()


LOGIN_SESSION_TTL_SEC = 600


@dataclass
class PendingLogin:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    created_at: float


_pending_logins: dict[int, PendingLogin] = {}


async def _close_pending_login(user_id: int) -> None:
    session = _pending_logins.pop(user_id, None)
    if not session:
        return
    try:
        await session.browser.close()
    except Exception:
        pass
    try:
        await session.playwright.stop()
    except Exception:
        pass


async def _cleanup_stale_logins() -> None:
    now = time.time()
    for uid in list(_pending_logins):
        if now - _pending_logins[uid].created_at > LOGIN_SESSION_TTL_SEC:
            await _close_pending_login(uid)


# ──────────────────────────── Auth guard ───────────────────────────────────

def _playwright_launch_kwargs() -> dict:
    return {
        "headless": True,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }


async def _new_login_context(p, storage_state: dict | None = None):
    ctx_kwargs: dict = {
        "user_agent": scraper._USER_AGENT,
        "viewport": {"width": 1366, "height": 768},
        "locale": "ru-RU",
    }
    if storage_state:
        ctx_kwargs["storage_state"] = storage_state
    browser = await p.chromium.launch(**_playwright_launch_kwargs())
    context = await browser.new_context(**ctx_kwargs)
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context

def only_owner(func):
    """Декоратор: бот отвечает только владельцу (ALLOWED_USER_ID из .env)."""
    sig = inspect.signature(func)

    @wraps(func)
    async def wrapper(*args, **kwargs):
        event = args[0] if args else None
        if event is None:
            for name in sig.parameters:
                if name in kwargs and hasattr(kwargs[name], "from_user"):
                    event = kwargs[name]
                    break
        uid = event.from_user.id if event and getattr(event, "from_user", None) else 0
        if uid != config.ALLOWED_USER_ID:
            return
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return await func(*args, **filtered)

    wrapper.__signature__ = sig
    return wrapper


# ─────────────────────────────── Handlers ──────────────────────────────────

@router.message(Command("start"))
@only_owner
async def cmd_start(msg: Message) -> None:
    await msg.answer(
        "👋 <b>HH.ru бот запущен</b>\n\n"
        "Команды:\n"
        "/import — войти на HH.ru через cookies (рекомендуется)\n"
        "/login — войти по SMS (часто ловит капчу)\n"
        "/search <i>запрос</i> — задать поиск (напр. <code>/search Python Backend</code>)\n"
        "/scan — сканировать вакансии прямо сейчас\n"
        "/status — статистика\n"
        "/resumes — выбрать резюме для откликов\n"
        "/threshold — порог совпадения (match %)\n\n"
        f"⏱ Автосканирование каждые {config.SCAN_INTERVAL_HOURS} ч.",
        parse_mode="HTML",
    )


# ──────── Авторизация ────────

@router.message(Command("login"))
@only_owner
async def cmd_login(msg: Message, state: FSMContext) -> None:
    await _close_pending_login(config.ALLOWED_USER_ID)
    await state.set_state(LoginFlow.waiting_for_phone)
    await msg.answer(
        "📱 Введи номер телефона от аккаунта HH.ru\n"
        "(в формате <code>+79001234567</code>):",
        parse_mode="HTML",
    )


@router.message(LoginFlow.waiting_for_phone)
@only_owner
async def login_phone_received(msg: Message, state: FSMContext) -> None:
    phone = (msg.text or "").strip()
    await _cleanup_stale_logins()
    await _close_pending_login(config.ALLOWED_USER_ID)

    status_msg = await msg.answer("⏳ Отправляю запрос на HH.ru...")

    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    browser, context = await _new_login_context(p)
    page = await context.new_page()
    ok = await scraper.login_step1_enter_phone(page, phone)

    await status_msg.delete()
    if ok:
        _pending_logins[config.ALLOWED_USER_ID] = PendingLogin(
            playwright=p,
            browser=browser,
            context=context,
            page=page,
            created_at=time.time(),
        )
        await state.set_state(LoginFlow.waiting_for_otp)
        await msg.answer(
            "📩 Код отправлен на телефон. Введи его сюда\n"
            f"(у тебя {LOGIN_SESSION_TTL_SEC // 60} минут):"
        )
    else:
        await browser.close()
        await p.stop()
        await state.clear()
        await msg.answer(
            "❌ Не удалось начать авторизацию.\n"
            "Проверь номер или попробуй /login ещё раз через минуту."
        )


@router.message(LoginFlow.waiting_for_otp)
@only_owner
async def login_otp_received(msg: Message, state: FSMContext) -> None:
    code = (msg.text or "").strip()
    session = _pending_logins.get(config.ALLOWED_USER_ID)

    status_msg = await msg.answer("⏳ Проверяю код...")
    ok = False

    if not session:
        await status_msg.delete()
        await state.clear()
        await msg.answer("⚠️ Сессия входа истекла. Запусти /login заново.")
        return

    if time.time() - session.created_at > LOGIN_SESSION_TTL_SEC:
        await _close_pending_login(config.ALLOWED_USER_ID)
        await status_msg.delete()
        await state.clear()
        await msg.answer("⚠️ Время на ввод кода истекло. Запусти /login заново.")
        return

    try:
        if not await scraper.login_has_otp_form(session.page):
            await msg.answer("⚠️ Форма ввода кода пропала. Запусти /login заново.")
            return
        ok = await scraper.login_step2_enter_otp(session.page, code)
        if ok:
            await scraper.save_session(session.context)
    finally:
        await _close_pending_login(config.ALLOWED_USER_ID)
        await status_msg.delete()
        await state.clear()

    if ok:
        await msg.answer("✅ Авторизация успешна! Сессия сохранена.")
    else:
        await msg.answer(
            "❌ Не удалось войти по SMS (HH.ru часто показывает капчу).\n"
            "Используй надёжный способ — /import (вход через cookies)."
        )


# ──────── Импорт сессии через cookies (обход робот-проверки) ────────

_IMPORT_INSTRUCTIONS = (
    "🔐 <b>Импорт входа через cookies</b> (обходит проверку «вы не робот»)\n\n"
    "Бот не логинится сам — ты заходишь на HH.ru в своём браузере, "
    "а боту отдаёшь готовые cookies.\n\n"
    "<b>Как сделать (5 минут, ничего не устанавливая на ПК):</b>\n"
    "1. Открой <a href='https://hh.ru'>hh.ru</a> в браузере и войди в аккаунт.\n"
    "2. Поставь расширение <b>Cookie-Editor</b> (Chrome/Edge/Firefox webstore).\n"
    "3. На вкладке hh.ru открой Cookie-Editor → внизу <b>Export</b> → "
    "<b>Export as JSON</b> (скопируется в буфер).\n"
    "4. Открой Блокнот, вставь (Ctrl+V), сохрани как <code>cookies.json</code>.\n"
    "5. Перетащи этот файл сюда, в чат с ботом.\n\n"
    "Я приму файл, проверю вход и сохраню сессию. 🎯"
)


@router.message(Command("import"))
@only_owner
async def cmd_import(msg: Message) -> None:
    cookies_path = Path(config.SESSION_FILE).parent / "hh_cookies.json"
    if cookies_path.exists():
        status = await msg.answer("⏳ Импортирую сессию из hh_cookies.json...")
        try:
            ok = await scraper.import_session_from_cookies(cookies_path.read_bytes())
        except Exception as e:
            await status.delete()
            await msg.answer(f"❌ Ошибка импорта: {e}")
            return
        await status.delete()
        await _report_import_result(msg, ok)
        return
    await msg.answer(_IMPORT_INSTRUCTIONS, parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.document)
@only_owner
async def on_cookies_document(msg: Message) -> None:
    doc = msg.document
    name = (doc.file_name or "").lower()
    if not name.endswith(".json"):
        await msg.answer("Пришли <b>.json</b> файл с cookies. Подробнее: /import", parse_mode="HTML")
        return

    status = await msg.answer("⏳ Импортирую сессию из файла...")
    try:
        file = await bot.get_file(doc.file_id)
        buf = await bot.download_file(file.file_path)
        content = buf.read()
        ok = await scraper.import_session_from_cookies(content)
    except Exception as e:
        await status.delete()
        await msg.answer(f"❌ Не удалось импортировать: {e}\n\nПроверь формат, см. /import")
        return
    await status.delete()
    await _report_import_result(msg, ok)


async def _report_import_result(msg: Message, ok: bool) -> None:
    if ok:
        await msg.answer(
            "✅ Сессия импортирована! Бот авторизован на HH.ru.\n"
            "Теперь: /resumes — выбрать резюме, /scan — искать вакансии."
        )
    else:
        await msg.answer(
            "⚠️ Cookies загружены, но HH.ru не распознал вход.\n"
            "Убедись, что:\n"
            "• экспортировал cookies, будучи <b>залогиненным</b> на hh.ru;\n"
            "• в файле есть cookie <code>hhtoken</code>;\n"
            "• экспорт именно с домена <b>hh.ru</b>.\n"
            "Затем пришли файл заново.",
            parse_mode="HTML",
        )


# ──────── Поиск ────────

@router.message(Command("search"))
@only_owner
async def cmd_search(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        current = await storage.get_setting("query", config.DEFAULT_QUERY)
        await msg.answer(
            f"Текущий запрос: <b>{current}</b>\n\n"
            "Чтобы изменить: <code>/search Python Backend Москва</code>",
            parse_mode="HTML",
        )
        return
    query = parts[1].strip()
    await storage.set_setting("query", query)
    await msg.answer(f"✅ Поисковый запрос сохранён:\n<b>{query}</b>", parse_mode="HTML")


# ──────── Статистика ────────

@router.message(Command("status"))
@only_owner
async def cmd_status(msg: Message) -> None:
    stats = await storage.get_stats()
    query = await storage.get_setting("query", config.DEFAULT_QUERY)
    ollama_ok = await llm.check_ollama()

    lines = [
        f"📊 <b>Статистика</b>",
        f"🔍 Запрос: <code>{query}</code>",
        f"🤖 Ollama ({config.OLLAMA_MODEL}): {'✅' if ollama_ok else '❌ недоступна'}",
        f"💾 Сессия HH.ru: {'✅' if Path(config.SESSION_FILE).exists() else '❌ нет (запусти /login)'}",
        "",
        f"📋 Всего вакансий в базе: {sum(stats.values())}",
        f"  ✅ Откликнулся: {stats.get('applied', 0)}",
        f"  ❌ Пропустил: {stats.get('skipped', 0)}",
        f"  🆕 Новых: {stats.get('new', 0)}",
        f"  👁 Показано: {stats.get('shown', 0)}",
        f"  ⚠️ Ниже порога: {stats.get('below_threshold', 0)}",
        f"  ⏭ Авто-пропуск (legacy): {stats.get('auto_skipped', 0)}",
        f"  💬 Ответили: {stats.get('responded', 0)}",
    ]
    active = await storage.get_active_resume()
    threshold = await storage.get_min_match_threshold(config.MIN_MATCH_THRESHOLD)
    if active:
        lines.insert(4, f"📄 Активное резюме: <b>{_esc(active['title'])}</b>")
    lines.insert(5, f"🎯 Порог совпадения: <b>{int(threshold * 100)}%</b>")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ──────── Резюме ────────

async def _active_ollama_model() -> str:
    """Модель Ollama из настроек (веб/БД) с фолбэком на .env."""
    return await storage.get_setting("ollama_model", config.OLLAMA_MODEL)


async def _parse_and_save_resume(resume_id: str, title: str = "") -> scraper.ResumeFullData:
    data = await scraper.parse_resume_full(resume_id)
    model = await _active_ollama_model()
    profile = await extractor.extract_resume_profile(data.raw_text, model=model)
    keywords = profile.all_skills()
    await storage.save_resume(
        data.id,
        data.title or title,
        data.raw_text,
        keywords,
        profile_json=profile.to_json(),
    )
    return data


@router.message(Command("resumes"))
@only_owner
async def cmd_resumes(msg: Message) -> None:
    await _send_resumes_list(msg)


async def _send_resumes_list(msg: Message) -> None:
    resumes = await storage.get_resumes()
    if not resumes:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Загрузить с HH.ru", callback_data="resumes:fetch")],
        ])
        await msg.answer(
            "📄 Резюме не найдены в базе.\nНажми кнопку, чтобы загрузить с HH.ru:",
            reply_markup=kb,
        )
        return

    lines = ["📄 <b>Твои резюме</b>\n"]
    buttons = []
    for r in resumes:
        mark = " ✅" if r["is_active"] else ""
        kw_count = len(r.get("keywords") or [])
        lines.append(f"• {_esc(r['title'])}{mark} ({kw_count} ключ. слов)")
        buttons.append([
            InlineKeyboardButton(
                text=f"{'✅ ' if r['is_active'] else ''}{r['title'][:30]}",
                callback_data=f"resume_select:{r['id']}",
            ),
        ])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить с HH.ru", callback_data="resumes:fetch")])
    await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "resumes:fetch")
@only_owner
async def cb_resumes_fetch(cq: CallbackQuery) -> None:
    await cq.answer("Загружаю резюме...")
    if not Path(config.SESSION_FILE).exists():
        await cq.message.answer("⚠️ Нет сессии HH.ru. Запусти /login.")
        return
    try:
        hh_resumes = await scraper.get_my_resumes()
    except RuntimeError as e:
        if "SESSION_EXPIRED" in str(e):
            await cq.message.answer("🔑 Сессия истекла. Запусти /login.")
        else:
            await cq.message.answer(f"❌ Ошибка: {e}")
        return
    except Exception as e:
        await cq.message.answer(f"❌ Ошибка: {e}")
        return

    for r in hh_resumes:
        await storage.save_resume(r.id, r.title)
    await cq.message.answer(f"✅ Загружено резюме: {len(hh_resumes)}")
    await _send_resumes_list(cq.message)


@router.callback_query(F.data.startswith("resume_select:"))
@only_owner
async def cb_resume_select(cq: CallbackQuery) -> None:
    resume_id = cq.data.split(":", 1)[1]
    await cq.answer("Парсю резюме...")
    await storage.set_active_resume(resume_id)
    try:
        data = await _parse_and_save_resume(resume_id)
        kw_preview = ", ".join((await storage.get_active_resume())["keywords"][:8])
        await cq.message.answer(
            f"✅ Активное резюме: <b>{_esc(data.title)}</b>\n"
            f"🔑 Ключевые слова: {kw_preview or '—'}",
            parse_mode="HTML",
        )
    except RuntimeError as e:
        if "SESSION_EXPIRED" in str(e):
            await cq.message.answer("🔑 Сессия истекла. Запусти /login.")
        else:
            await cq.message.answer(f"❌ Ошибка парсинга: {e}")
    except Exception as e:
        await cq.message.answer(f"❌ Ошибка: {e}")


# ──────── Порог совпадения ────────

@router.message(Command("threshold"))
@only_owner
async def cmd_threshold(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    current = await storage.get_min_match_threshold(config.MIN_MATCH_THRESHOLD)
    if len(parts) < 2:
        await msg.answer(
            f"🎯 Текущий порог: <b>{int(current * 100)}%</b>\n\n"
            "Изменить: <code>/threshold 65</code> (0–100)",
            parse_mode="HTML",
        )
        return
    try:
        pct = int(parts[1].strip().replace("%", ""))
        if not 0 <= pct <= 100:
            raise ValueError
        await storage.set_setting("min_match_threshold", str(pct / 100))
        await msg.answer(f"✅ Порог совпадения: <b>{pct}%</b>", parse_mode="HTML")
    except ValueError:
        await msg.answer("❌ Укажи число от 0 до 100, напр. <code>/threshold 65</code>", parse_mode="HTML")


# ──────── Ручной скан ────────

@router.message(Command("scan"))
@only_owner
async def cmd_scan(msg: Message) -> None:
    await msg.answer("🔍 Запускаю сканирование...")
    await run_scan()


# ─────────────────────────── Scan & Notify ─────────────────────────────────

async def run_scan() -> None:
    """
    Основная функция сканирования:
    1. Получает список вакансий через Playwright
    2. Фильтрует уже виденные (по БД)
    3. Санитизирует текст (защита от prompt injection)
    4. Отправляет в LLM для анализа
    5. Шлёт карточку в Telegram
    """
    if scan_state.running:
        log.info("Скан уже выполняется — пропускаю повторный запуск")
        return

    query = await storage.get_setting("query", config.DEFAULT_QUERY)
    log.info("Скан запущен: query=%r", query)
    scan_state.reset(query)

    if not Path(config.SESSION_FILE).exists():
        scan_state.error = "Нет сессии HH.ru"
        scan_state.log("⚠️ Нет сессии HH.ru — нужен /import или /login")
        scan_state.finish("error", "Нет сессии HH.ru")
        await bot.send_message(
            config.ALLOWED_USER_ID,
            "⚠️ Нет сессии HH.ru. Запусти /login сначала.",
        )
        return

    model = await _active_ollama_model()
    ollama_ok = await llm.check_ollama(model)
    if not ollama_ok:
        scan_state.error = "LLM недоступна"
        scan_state.log("⚠️ Ollama недоступна")
        scan_state.finish("error", "LLM недоступна")
        await bot.send_message(
            config.ALLOWED_USER_ID,
            f"⚠️ Ollama недоступна. Убедись, что она запущена:\n"
            f"<code>ollama serve</code>\n"
            f"<code>ollama pull {model}</code>",
            parse_mode="HTML",
        )
        return

    try:
        scan_state.log("Открываю HH.ru и собираю вакансии…")
        vacancies = await scraper.search_vacancies(query, limit=config.MAX_VACANCIES)
        scan_state.log(f"Найдено вакансий: {len(vacancies)}")
    except RuntimeError as e:
        scan_state.error = str(e)
        scan_state.finish("error", "Ошибка парсера")
        if "SESSION_EXPIRED" in str(e):
            await bot.send_message(
                config.ALLOWED_USER_ID,
                "🔑 Сессия HH.ru истекла. Запусти /login заново.",
            )
        else:
            await bot.send_message(config.ALLOWED_USER_ID, f"❌ Ошибка парсера: {e}")
        return
    except Exception as e:
        log.exception("Ошибка парсера")
        scan_state.error = str(e)
        scan_state.finish("error", "Ошибка парсера")
        await bot.send_message(config.ALLOWED_USER_ID, f"❌ Ошибка парсера: {e}")
        return

    new_count = 0
    below_threshold = 0
    exp_skipped = 0
    threshold = await storage.get_min_match_threshold(config.MIN_MATCH_THRESHOLD)

    # Фильтр по опыту
    exp_filter_setting = await storage.get_setting("experience_filter", "")
    if exp_filter_setting:
        exp_filter_on = exp_filter_setting.lower() in ("1", "true", "yes", "on")
    else:
        exp_filter_on = config.EXPERIENCE_FILTER_ENABLED

    try:
        exp_tolerance = float(
            await storage.get_setting("experience_tolerance_years", str(config.EXPERIENCE_TOLERANCE_YEARS))
        )
    except (ValueError, TypeError):
        exp_tolerance = config.EXPERIENCE_TOLERANCE_YEARS

    active_resume = await storage.get_active_resume()
    resume_years: float | None = None
    if active_resume:
        resume_years = experience.parse_resume_years(active_resume.get("raw_text", ""))
    if exp_filter_on and resume_years is not None:
        scan_state.log(f"🧮 Стаж по резюме: ~{resume_years} лет (фильтр опыта включён)")

    resume_profile = ResumeProfile()
    if active_resume:
        resume_profile = ResumeProfile.from_json(active_resume.get("profile_json") or "")
        if not resume_profile.all_skills() and active_resume.get("raw_text"):
            resume_profile = await extractor.extract_resume_profile(
                active_resume["raw_text"], model=model
            )
            await storage.save_resume(
                active_resume["id"],
                active_resume["title"],
                active_resume["raw_text"],
                resume_profile.all_skills(),
                profile_json=resume_profile.to_json(),
            )

    scan_state.total = len(vacancies)
    scan_state.set_phase("matching", "Анализ вакансий")

    for v in vacancies:
        scan_state.current_title = v.title
        scan_state.current_company = v.company
        scan_state.processed += 1

        if await storage.is_seen(v.id):
            scan_state.log(f"↺ Уже видели: {v.title}")
            continue

        # Фильтр по опыту — до дорогого LLM-анализа и без записи в статистику
        if exp_filter_on and resume_years is not None:
            req_years = experience.parse_required_years(v.experience)
            if req_years is None and v.full_text:
                req_years = experience.parse_required_years(v.full_text)
            if not experience.is_experience_ok(
                req_years, resume_years, exp_tolerance
            ):
                exp_skipped += 1
                scan_state.skipped_count = below_threshold + exp_skipped
                scan_state.log(
                    f"⏭ Опыт {v.experience or req_years} > резюме (~{resume_years}л) — пропуск: {v.title}"
                )
                continue

        match_result = None
        vac_profile = VacancyProfile()
        if active_resume:
            san = sanitizer.sanitize(v.full_text or v.title)
            vac_text = san.text if san.text else v.title
            scan_state.log(f"🔍 Парсинг требований: {v.title}")
            vac_profile = await extractor.extract_vacancy_requirements(
                v.title, v.company, vac_text, model=model,
            )
            match_result = await matcher.compute_match(
                vac_profile,
                resume_profile,
                vacancy_text=vac_text,
                resume_text=active_resume.get("raw_text", ""),
                threshold=threshold,
            )
            if match_result.verdict == "SKIP":
                below_threshold += 1
                scan_state.skipped_count = below_threshold
                scan_state.log(f"⚠️ {match_result.score_pct}% — ниже порога: {v.title}")

        new_count += 1
        scan_state.new_count = new_count
        pct = f"{match_result.score_pct}% — " if match_result else ""
        scan_state.log(f"✅ {pct}отправляю: {v.title} ({v.company})")
        resume_id = active_resume["id"] if active_resume else ""
        await _process_and_notify(v, match_result, query, vac_profile, model, resume_id)
        await asyncio.sleep(2)

    scan_state.current_title = ""
    scan_state.current_company = ""
    scan_state.log(
        f"Готово. Новых: {new_count}, ниже порога: {below_threshold}, "
        f"пропущено по опыту: {exp_skipped}"
    )
    scan_state.finish("done", "Скан завершён")

    if new_count == 0:
        msg = "😴 Новых вакансий не найдено."
        if exp_skipped:
            msg += f"\n⏭ Отсеяно по требуемому опыту: {exp_skipped}"
        await bot.send_message(config.ALLOWED_USER_ID, msg)
    else:
        log.info(
            "Отправлено карточек: %d, ниже порога: %d, пропущено по опыту: %d",
            new_count, below_threshold, exp_skipped,
        )


async def _process_and_notify(
    v: scraper.VacancyData,
    match_result: matcher.MatchResult | None = None,
    scan_query: str = "",
    vac_profile: VacancyProfile | None = None,
    model: str | None = None,
    resume_id: str = "",
) -> None:
    """Обрабатывает одну вакансию: санитизирует → LLM → Telegram."""

    san = sanitizer.sanitize(v.full_text)
    text_for_llm = san.text if san.text else v.title

    summary = await llm.analyze_vacancy(
        title=v.title,
        company=v.company,
        salary=v.salary,
        text=text_for_llm,
        model=model or config.OLLAMA_MODEL,
    )

    status = "below_threshold" if match_result and match_result.verdict == "SKIP" else "shown"

    await storage.save_vacancy(
        vacancy_id=v.id,
        title=v.title,
        company=v.company,
        url=v.url,
        salary=v.salary,
        summary=summary,
        status=status,
        match_score=match_result.score if match_result else 0.0,
        matched_skills=match_result.matched if match_result else [],
        missing_skills=match_result.missing if match_result else [],
        extra_skills=match_result.extra if match_result else [],
        profile_json=vac_profile.to_json() if vac_profile else "",
        scan_query=scan_query,
        resume_id=resume_id,
    )

    inject_warn = ""
    if san.is_suspicious:
        inject_warn = (
            "\n\n⚠️ <b>Внимание:</b> в тексте вакансии обнаружены "
            f"подозрительные фрагменты ({', '.join(san.found_tags)}). "
            "Они нейтрализованы."
        )

    header = (
        f"🏢 <b>{_esc(v.company)}</b>\n"
        f"💼 <b>{_esc(v.title)}</b>\n"
        f"💰 {_esc(v.salary) or 'зарплата не указана'}\n"
    )
    if match_result:
        header += f"{matcher.format_match_line(match_result)}\n"
    header += f"🔗 <a href='{v.url}'>Открыть вакансию</a>"

    text = f"{header}\n\n{_esc(summary)}{inject_warn}"
    kb = _build_vacancy_keyboard(v, match_result)

    try:
        await bot.send_message(
            config.ALLOWED_USER_ID,
            text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Ошибка отправки в Telegram: %s", e)


def _build_vacancy_keyboard(
    v: scraper.VacancyData,
    match_result: matcher.MatchResult | None,
) -> InlineKeyboardMarkup:
    if match_result and match_result.verdict == "PASS":
        action_row = [
            InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply:{v.id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{v.id}"),
        ]
    elif match_result:
        action_row = [
            InlineKeyboardButton(
                text="⚠️ Откликнуться всё равно",
                callback_data=f"apply_force:{v.id}",
            ),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{v.id}"),
        ]
    else:
        action_row = [
            InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply:{v.id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{v.id}"),
        ]
    return InlineKeyboardMarkup(inline_keyboard=[
        action_row,
        [InlineKeyboardButton(text="🔗 Открыть", url=v.url)],
    ])


def _letter_preview_keyboard(vacancy_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отклик", callback_data=f"letter_send:{vacancy_id}")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"letter_edit:{vacancy_id}")],
        [InlineKeyboardButton(text="🚫 Без письма", callback_data=f"letter_skip:{vacancy_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"letter_cancel:{vacancy_id}")],
    ])


def _esc(text: str) -> str:
    """Минимальный HTML escape для Telegram."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ─────────────────── Callback: Откликнуться / Пропустить ───────────────────

async def _start_apply_flow(cq: CallbackQuery, vacancy_id: str) -> None:
    vacancy = await storage.get_vacancy(vacancy_id)
    if not vacancy:
        await cq.message.reply("❌ Вакансия не найдена в базе.")
        return

    active = await storage.get_active_resume()
    vac_profile = VacancyProfile.from_json(vacancy.get("profile_json") or "")
    res_profile = ResumeProfile()
    if active:
        res_profile = ResumeProfile.from_json(active.get("profile_json") or "")

    matched = vacancy.get("matched_skills") or []
    await cq.answer("Генерирую письмо...")
    model = await _active_ollama_model()
    cl_backend = await storage.get_setting("cover_letter_backend", "")
    cl_api_key = await storage.get_setting("anthropic_api_key", "")
    cover = await letter.generate_cover_letter(
        vac_profile,
        res_profile,
        matched,
        title=vacancy.get("title", ""),
        company=vacancy.get("company", ""),
        model=model,
        backend=cl_backend,
        api_key=cl_api_key,
    )
    await storage.set_cover_letter(vacancy_id, cover)

    preview = cover[:3500]
    await cq.message.reply(
        f"📝 <b>Сопроводительное письмо:</b>\n\n{_esc(preview)}",
        parse_mode="HTML",
        reply_markup=_letter_preview_keyboard(vacancy_id),
    )


@router.callback_query(F.data.startswith("apply:"))
@only_owner
async def cb_apply(cq: CallbackQuery) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    await _start_apply_flow(cq, vacancy_id)


@router.callback_query(F.data.startswith("apply_force:"))
@only_owner
async def cb_apply_force(cq: CallbackQuery) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    await _start_apply_flow(cq, vacancy_id)


@router.callback_query(F.data.startswith("letter_send:"))
@only_owner
async def cb_letter_send(cq: CallbackQuery) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    vacancy = await storage.get_vacancy(vacancy_id)
    cover = (vacancy or {}).get("cover_letter", "")
    await cq.answer("Отправляю отклик...")
    await cq.message.edit_reply_markup(reply_markup=None)
    ok = await _playwright_apply(f"https://hh.ru/vacancy/{vacancy_id}", cover)
    if ok:
        await storage.update_status(vacancy_id, "applied")
        await cq.message.reply("✅ Отклик отправлен!")
    else:
        await cq.message.reply(
            "⚠️ Не удалось откликнуться автоматически "
            f"(нужен тест или ручной ввод).\n🔗 https://hh.ru/vacancy/{vacancy_id}"
        )


@router.callback_query(F.data.startswith("letter_skip:"))
@only_owner
async def cb_letter_skip(cq: CallbackQuery) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    await cq.answer("Отправляю без письма...")
    await cq.message.edit_reply_markup(reply_markup=None)
    ok = await _playwright_apply(f"https://hh.ru/vacancy/{vacancy_id}", "")
    if ok:
        await storage.update_status(vacancy_id, "applied")
        await cq.message.reply("✅ Отклик отправлен (без письма)!")
    else:
        await cq.message.reply(
            "⚠️ Не удалось откликнуться автоматически.\n"
            f"🔗 https://hh.ru/vacancy/{vacancy_id}"
        )


@router.callback_query(F.data.startswith("letter_edit:"))
@only_owner
async def cb_letter_edit(cq: CallbackQuery, state: FSMContext) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    await state.set_state(ApplyFlow.waiting_for_letter_edit)
    await state.update_data(vacancy_id=vacancy_id)
    await cq.answer()
    await cq.message.reply("✏️ Отправь отредактированный текст письма одним сообщением:")


@router.message(ApplyFlow.waiting_for_letter_edit)
@only_owner
async def msg_letter_edit(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    vacancy_id = data.get("vacancy_id", "")
    if not vacancy_id:
        await state.clear()
        return
    await storage.set_cover_letter(vacancy_id, msg.text or "")
    await state.clear()
    await msg.answer(
        "✅ Письмо обновлено. Подтвердите отправку:",
        reply_markup=_letter_preview_keyboard(vacancy_id),
    )


@router.callback_query(F.data.startswith("letter_cancel:"))
@only_owner
async def cb_letter_cancel(cq: CallbackQuery) -> None:
    await cq.answer("Отменено")
    await cq.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("skip:"))
@only_owner
async def cb_skip(cq: CallbackQuery) -> None:
    vacancy_id = cq.data.split(":", 1)[1]
    await storage.update_status(vacancy_id, "skipped")
    await cq.answer("Пропущено")
    await cq.message.edit_reply_markup(reply_markup=None)


async def _playwright_apply(vacancy_url: str, cover_letter: str = "") -> bool:
    """
    Открывает страницу вакансии и нажимает «Откликнуться».
    При необходимости заполняет сопроводительное письмо.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    async with scraper.BROWSER_LOCK:
        p, browser, context = await scraper._new_browser_context()
        page = await context.new_page()

        try:
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)

            # Уже откликались ранее
            if await page.locator('[data-qa="vacancy-response-link-top-again"]').count() > 0:
                return True

            # Кнопка "Откликнуться"
            btn = page.locator('[data-qa="vacancy-response-link-top"]').first
            if await btn.count() == 0:
                return False

            await btn.click()
            await asyncio.sleep(3)

            # Скрининг/тест — редирект на отдельную страницу (не умеем автоматизировать)
            if any(s in page.url for s in ("/applicant/vacancy_response", "questionnaire", "quiz")):
                return False

            # Модалка отклика (2026): submit = vacancy-response-submit-popup
            submit = page.locator('[data-qa="vacancy-response-submit-popup"]').first
            try:
                await submit.wait_for(state="visible", timeout=8_000)
            except PWTimeout:
                # Возможно отклик ушёл сразу без модалки
                if await _apply_succeeded(page):
                    await scraper.save_session(context)
                    return True
                return False

            # Если в модалке есть выбор резюме — выбираем активное
            active = await storage.get_active_resume()
            if active:
                radio = page.locator(
                    f'[data-qa="resume-title-{active["id"]}"], [data-resume-hash="{active["id"]}"]'
                )
                if await radio.count() > 0:
                    try:
                        await radio.first.click()
                        await asyncio.sleep(1)
                    except Exception:
                        pass

            if cover_letter:
                add_btn = page.locator('[data-qa="add-cover-letter"]')
                if await add_btn.count() > 0:
                    try:
                        await add_btn.first.click()
                        await asyncio.sleep(1)
                    except Exception:
                        pass
                textarea = page.locator(
                    'textarea[data-qa="vacancy-response-popup-form-letter-input"], '
                    'textarea[name="letter"], '
                    '[data-qa="vacancy-response-popup-form-letter-input"] textarea, '
                    '.vacancy-response-popup-form textarea'
                )
                if await textarea.count() > 0:
                    try:
                        await textarea.first.fill(cover_letter)
                        await asyncio.sleep(1)
                    except Exception:
                        log.warning("Не удалось заполнить сопроводительное письмо")

            await submit.click()
            await asyncio.sleep(3)

            if await _apply_succeeded(page):
                await scraper.save_session(context)
                return True

            return False

        except PWTimeout:
            return False
        finally:
            await browser.close()
            await p.stop()


async def _apply_succeeded(page) -> bool:
    """Проверяет признаки успешного отклика (hh.ru 2026)."""
    for sel in (
        '[data-qa="vacancy-response-success-standard-notification"]',
        '[data-qa="vacancy-response-link-top-again"]',
        'text=Отклик отправлен',
        'text=Вы откликнулись',
    ):
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


# ─────────────────────────── Entry point ───────────────────────────────────

async def check_responses_job() -> None:
    """Планировщик: проверяет ответы работодателей."""
    if not Path(config.SESSION_FILE).exists():
        return
    try:
        vacancy_ids = await scraper.check_responses()
    except Exception:
        log.exception("Ошибка check_responses")
        return

    for vid in vacancy_ids:
        existing = await storage.get_vacancy(vid)
        if existing and existing.get("status") != "responded":
            await storage.mark_response_received(vid)
            await bot.send_message(
                config.ALLOWED_USER_ID,
                f"💬 <b>Ответ от работодателя!</b>\n"
                f"🏢 {_esc(existing.get('company', ''))}\n"
                f"💼 {_esc(existing.get('title', ''))}\n"
                f"🔗 https://hh.ru/vacancy/{vid}",
                parse_mode="HTML",
            )


async def reparse_active_resume_job() -> None:
    """Ежедневное обновление ключевых слов активного резюме."""
    active = await storage.get_active_resume()
    if not active or not Path(config.SESSION_FILE).exists():
        return
    try:
        await _parse_and_save_resume(active["id"], active["title"])
        log.info("Ключевые слова резюме обновлены")
    except Exception:
        log.exception("Ошибка перепарсинга резюме")


def reschedule_scan(hours: int) -> None:
    """Меняет интервал авто-скана на лету (вызывается из веб-настроек)."""
    if hours < 1:
        hours = 1
    try:
        scheduler.reschedule_job("auto_scan", trigger="interval", hours=hours)
        log.info("Интервал авто-скана изменён: каждые %d ч.", hours)
    except Exception:
        log.exception("Не удалось изменить интервал авто-скана")


def _validate_config() -> None:
    """Проверяет обязательные параметры окружения до запуска."""
    problems = []
    if not config.TELEGRAM_TOKEN:
        problems.append("TELEGRAM_TOKEN не задан")
    if not config.ALLOWED_USER_ID:
        problems.append("ALLOWED_USER_ID не задан (0)")
    if problems:
        raise SystemExit(
            "Ошибка конфигурации:\n  - " + "\n  - ".join(problems)
            + "\nЗаполни .env (см. .env.example) и перезапусти."
        )


async def main() -> None:
    _validate_config()
    await storage.init_db()

    if not await storage.get_setting("min_match_threshold"):
        await storage.set_setting("min_match_threshold", str(config.MIN_MATCH_THRESHOLD))

    interval_raw = await storage.get_setting(
        "scan_interval_hours", str(config.SCAN_INTERVAL_HOURS)
    )
    try:
        scan_interval = max(1, int(interval_raw))
    except ValueError:
        scan_interval = config.SCAN_INTERVAL_HOURS

    scheduler.add_job(
        run_scan,
        trigger="interval",
        hours=scan_interval,
        id="auto_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        check_responses_job,
        trigger="interval",
        hours=1,
        id="check_responses",
        replace_existing=True,
    )
    scheduler.add_job(
        reparse_active_resume_job,
        trigger="interval",
        hours=24,
        id="reparse_resume",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Планировщик запущен: каждые %d ч.", scan_interval)

    from web.app import create_app
    import uvicorn

    web_app = create_app()
    config_uv = uvicorn.Config(
        web_app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning"
    )
    server = uvicorn.Server(config_uv)

    log.info("Бот и веб-интерфейс запущены (http://%s:%d).", config.WEB_HOST, config.WEB_PORT)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await asyncio.gather(
            server.serve(),
            dp.start_polling(bot),
        )
    finally:
        log.info("Останавливаю бота…")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await _close_pending_login(config.ALLOWED_USER_ID)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        if str(e):
            print(e)
