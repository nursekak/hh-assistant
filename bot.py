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
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import storage
import scraper
from services import (
    ApplyService,
    DashboardService,
    JobQueue,
    ResponseService,
    ResumeService,
    ScanNotifier,
    ScanService,
    SettingsService,
)
from services.telegram_ui import build_letter_preview_keyboard, esc

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

settings_service = SettingsService()
resume_service = ResumeService()
apply_service = ApplyService()
response_service = ResponseService()
dashboard_service = DashboardService()
job_queue = JobQueue(config.REDIS_URL)


def _scan_service() -> ScanService:
    return ScanService(
        notifier=ScanNotifier(
            send_message=bot.send_message,
            user_id=config.ALLOWED_USER_ID,
        ),
    )


# ─────────────────── Enqueue helpers (очередь ↔ inline fallback) ────────────

async def enqueue_scan() -> None:
    await job_queue.enqueue("scan_task", fallback=lambda: _scan_service().run())


async def _inline_check_responses() -> None:
    await response_service.check_and_notify(bot.send_message, config.ALLOWED_USER_ID)


async def _inline_reparse() -> None:
    active = await resume_service.resume_repo.get_active()
    if not active or not Path(config.SESSION_FILE).exists():
        return
    try:
        await resume_service.reparse(active["id"])
        log.info("Ключевые слова резюме обновлены")
    except Exception:
        log.exception("Ошибка перепарсинга резюме")


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


# ─────────────────────────── Меню / кнопки ─────────────────────────────────

# Подписи кнопок reply-клавиатуры. Каждая дублирует команду — обработчики ниже
# слушают и /команду, и текст кнопки (через стек декораторов @router.message).
BTN_SCAN      = "🔍 Сканировать"
BTN_STATUS    = "📊 Статистика"
BTN_RESUMES   = "📄 Резюме"
BTN_THRESHOLD = "🎯 Порог"
BTN_SEARCH    = "🔎 Запрос"
BTN_IMPORT    = "🔐 Вход"
BTN_HELP      = "ℹ️ Меню"

# Список команд для нативного меню Telegram (кнопка «Menu» / «/»).
BOT_COMMANDS = [
    BotCommand(command="scan",      description="🔍 Сканировать вакансии сейчас"),
    BotCommand(command="status",    description="📊 Статистика"),
    BotCommand(command="resumes",   description="📄 Выбрать резюме"),
    BotCommand(command="threshold", description="🎯 Порог совпадения"),
    BotCommand(command="search",    description="🔎 Поисковый запрос"),
    BotCommand(command="import",    description="🔐 Вход через cookies"),
    BotCommand(command="login",     description="📱 Вход по SMS"),
    BotCommand(command="menu",      description="ℹ️ Показать меню"),
]


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянная reply-клавиатура с основными действиями."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_RESUMES), KeyboardButton(text=BTN_THRESHOLD)],
            [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_IMPORT)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери действие или введи команду…",
    )


# ─────────────────────────────── Handlers ──────────────────────────────────

@router.message(Command("start"))
@router.message(Command("menu"))
@router.message(F.text == BTN_HELP)
@only_owner
async def cmd_start(msg: Message) -> None:
    await msg.answer(
        "👋 <b>HH.ru бот запущен</b>\n\n"
        "Жми кнопки внизу или используй команды:\n"
        "/import — войти на HH.ru через cookies (рекомендуется)\n"
        "/login — войти по SMS (часто ловит капчу)\n"
        "/search <i>запрос</i> — задать поиск (напр. <code>/search Python Backend</code>)\n"
        "/scan — сканировать вакансии прямо сейчас\n"
        "/status — статистика\n"
        "/resumes — выбрать резюме для откликов\n"
        "/threshold — порог совпадения (match %)\n\n"
        f"⏱ Автосканирование каждые {config.SCAN_INTERVAL_HOURS} ч.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
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
@router.message(F.text == BTN_IMPORT)
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
@router.message(F.text == BTN_SEARCH)
@only_owner
async def cmd_search(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        view = await settings_service.get_view()
        await msg.answer(
            f"Текущий запрос: <b>{view.query}</b>\n\n"
            "Чтобы изменить: <code>/search Python Backend Москва</code>",
            parse_mode="HTML",
        )
        return
    query = parts[1].strip()
    await settings_service.set_query(query)
    await msg.answer(f"✅ Поисковый запрос сохранён:\n<b>{query}</b>", parse_mode="HTML")


# ──────── Статистика ────────

@router.message(Command("status"))
@router.message(F.text == BTN_STATUS)
@only_owner
async def cmd_status(msg: Message) -> None:
    ctx = await dashboard_service.get_bot_status()
    stats = ctx["stats"]
    active = ctx["active_resume"]

    lines = [
        "📊 <b>Статистика</b>",
        f"🔍 Запрос: <code>{ctx['query']}</code>",
        f"🤖 Ollama ({ctx['ollama_model']}): {'✅' if ctx['ollama_ok'] else '❌ недоступна'}",
        f"💾 Сессия HH.ru: {'✅' if ctx['session_ok'] else '❌ нет (запусти /login)'}",
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
    if active:
        lines.insert(4, f"📄 Активное резюме: <b>{esc(active['title'])}</b>")
    lines.insert(5, f"🎯 Порог совпадения: <b>{ctx['threshold_pct']}%</b>")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ──────── Резюме ────────

@router.message(Command("resumes"))
@router.message(F.text == BTN_RESUMES)
@only_owner
async def cmd_resumes(msg: Message) -> None:
    await _send_resumes_list(msg)


async def _send_resumes_list(msg: Message) -> None:
    resumes = await resume_service.resume_repo.list()
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
        lines.append(f"• {esc(r['title'])}{mark} ({kw_count} ключ. слов)")
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
        count = await resume_service.fetch_from_hh()
    except RuntimeError as e:
        if "SESSION_EXPIRED" in str(e):
            await cq.message.answer("🔑 Сессия истекла. Запусти /login.")
        else:
            await cq.message.answer(f"❌ Ошибка: {e}")
        return
    except Exception as e:
        await cq.message.answer(f"❌ Ошибка: {e}")
        return

    await cq.message.answer(f"✅ Загружено резюме: {count}")
    await _send_resumes_list(cq.message)


@router.callback_query(F.data.startswith("resume_select:"))
@only_owner
async def cb_resume_select(cq: CallbackQuery) -> None:
    resume_id = cq.data.split(":", 1)[1]
    await cq.answer("Парсю резюме...")
    try:
        await resume_service.activate(resume_id)
        active = await resume_service.resume_repo.get_active()
        kw_preview = ", ".join((active or {}).get("keywords", [])[:8])
        title = (active or {}).get("title", resume_id)
        await cq.message.answer(
            f"✅ Активное резюме: <b>{esc(title)}</b>\n"
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
@router.message(F.text == BTN_THRESHOLD)
@only_owner
async def cmd_threshold(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    view = await settings_service.get_view()
    if len(parts) < 2:
        await msg.answer(
            f"🎯 Текущий порог: <b>{view.threshold_pct}%</b>\n\n"
            "Изменить: <code>/threshold 65</code> (0–100)",
            parse_mode="HTML",
        )
        return
    try:
        pct = int(parts[1].strip().replace("%", ""))
        if not 0 <= pct <= 100:
            raise ValueError
        await settings_service.set_match_threshold(pct)
        await msg.answer(f"✅ Порог совпадения: <b>{pct}%</b>", parse_mode="HTML")
    except ValueError:
        await msg.answer("❌ Укажи число от 0 до 100, напр. <code>/threshold 65</code>", parse_mode="HTML")


# ──────── Ручной скан ────────

@router.message(Command("scan"))
@router.message(F.text == BTN_SCAN)
@only_owner
async def cmd_scan(msg: Message) -> None:
    if await dashboard_service.is_scan_running():
        await msg.answer("⏳ Скан уже выполняется. Дождись завершения.")
        return
    await msg.answer("🔍 Ставлю сканирование в очередь...")
    await enqueue_scan()


# ─────────────────────────── Scan & Notify ─────────────────────────────────

async def run_scan() -> None:
    """Точка входа планировщика: ставит задачу в очередь (или inline)."""
    await enqueue_scan()


# ─────────────────── Callback: Откликнуться / Пропустить ───────────────────

async def _start_apply_flow(cq: CallbackQuery, vacancy_id: str) -> None:
    await cq.answer("Генерирую письмо...")
    result = await apply_service.generate_cover_letter(vacancy_id)
    if not result:
        await cq.message.reply("❌ Вакансия не найдена в базе.")
        return
    _, cover = result
    preview = cover[:3500]
    await cq.message.reply(
        f"📝 <b>Сопроводительное письмо:</b>\n\n{esc(preview)}",
        parse_mode="HTML",
        reply_markup=build_letter_preview_keyboard(vacancy_id),
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
    await cq.answer("Отправляю отклик...")
    await cq.message.edit_reply_markup(reply_markup=None)
    ok = await apply_service.submit_application(vacancy_id, with_letter=True)
    if ok:
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
    ok = await apply_service.submit_application(vacancy_id, with_letter=False)
    if ok:
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
    await apply_service.update_cover_letter(vacancy_id, msg.text or "")
    await state.clear()
    await msg.answer(
        "✅ Письмо обновлено. Подтвердите отправку:",
        reply_markup=build_letter_preview_keyboard(vacancy_id),
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
    await apply_service.skip_vacancy(vacancy_id)
    await cq.answer("Пропущено")
    await cq.message.edit_reply_markup(reply_markup=None)


# ─────────────────────────── Entry point ───────────────────────────────────

async def check_responses_job() -> None:
    """Планировщик: ставит проверку ответов в очередь (или inline)."""
    await job_queue.enqueue("check_responses_task", fallback=_inline_check_responses)


async def reparse_active_resume_job() -> None:
    """Планировщик: ставит перепарсинг резюме в очередь (или inline)."""
    await job_queue.enqueue("reparse_resume_task", fallback=_inline_reparse)


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
    if job_queue.enabled:
        # Скан выполняет воркер — он и сбрасывает свои зависшие задачи на старте.
        # Бот не трогает running-строки, иначе рестарт бота убьёт активный скан.
        log.info("Очередь задач: Redis (%s)", config.REDIS_URL)
    else:
        reaped = await storage.reset_orphaned_scan_jobs()
        if reaped:
            log.info("Сброшено зависших задач скана: %d", reaped)
        log.info("Очередь задач: inline (REDIS_URL не задан)")

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
        await bot.set_my_commands(BOT_COMMANDS)
        await asyncio.gather(
            server.serve(),
            dp.start_polling(bot),
        )
    finally:
        log.info("Останавливаю бота…")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await _close_pending_login(config.ALLOWED_USER_ID)
        await job_queue.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        if str(e):
            print(e)
