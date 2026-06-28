"""
Парсер вакансий HH.ru через Playwright.

Поскольку API для соискателей закрыт с 15.12.2025, эмулируем браузер:
- Ищем вакансии по запросу
- Скроллим для подгрузки всех карточек
- Для каждой карточки открываем вакансию и забираем полный текст
- Сохраняем сессию в JSON (чтобы не логиниться каждый раз)
"""

import asyncio
import json
import logging
import os
import random
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

from config import SESSION_FILE, MAX_VACANCIES
import storage

log = logging.getLogger(__name__)

# Случайный User-Agent, чтобы выглядеть как обычный пользователь
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Аргументы запуска Chromium: --no-sandbox обязателен в Docker,
# отключение AutomationControlled прячет признаки бота.
_LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]

_CONTEXT_KWARGS = {
    "user_agent": _USER_AGENT,
    "viewport": {"width": 1366, "height": 768},
    "locale": "ru-RU",
}

_STEALTH_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)

# Сериализует доступ к браузеру и файлу сессии: бот однопользовательский,
# но скан / проверка ответов / отклик / парсинг резюме могут пересечься во
# времени и одновременно писать SESSION_FILE → порча сессии. Лок это исключает.
# Гибридный: внутри процесса — asyncio.Lock, между процессами (бот ↔ воркер) —
# распределённый Redis-лок, если задан REDIS_URL.
from distributed_lock import HybridLock

BROWSER_LOCK = HybridLock("hh:browser")


def _context_kwargs(use_session: bool = True) -> dict:
    kwargs = dict(_CONTEXT_KWARGS)
    if use_session and Path(SESSION_FILE).exists():
        kwargs["storage_state"] = SESSION_FILE
    return kwargs


async def save_session(context: BrowserContext) -> None:
    """Атомарно сохраняет storage_state в SESSION_FILE (temp + os.replace)."""
    state = await context.storage_state()
    target = Path(SESSION_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@dataclass
class VacancyData:
    id: str
    title: str
    company: str
    salary: str
    url: str
    full_text: str = field(default="")
    experience: str = field(default="")


@dataclass
class ResumeInfo:
    id: str
    title: str
    specialization: str = ""


@dataclass
class ResumeFullData:
    id: str
    title: str
    skills: list[str]
    raw_text: str
    specialization: str = ""


# ---------- Вспомогательные функции ----------

async def _random_delay(min_ms: int = 1500, max_ms: int = 3500) -> None:
    """Случайная пауза — имитируем человека, не долбим сервер."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _scroll_to_load_all(page: Page, max_scrolls: int = 8) -> None:
    """Скроллим вниз, пока не перестанут подгружаться карточки."""
    prev_count = 0
    stable_rounds = 0

    for _ in range(max_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.2)
        cur_count = await page.locator('[data-qa="vacancy-serp__vacancy"]').count()

        if cur_count == prev_count:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
            prev_count = cur_count


async def _get_full_text(page: Page, url: str) -> tuple[str, str]:
    """Открывает страницу вакансии и возвращает (текст описания, требуемый опыт).

    Опыт берём из структурированного блока HH (data-qa="vacancy-experience"),
    он стандартизирован: «Без опыта», «От 1 года до 3 лет», «От 3 до 6 лет», «Более 6 лет».
    """
    text = ""
    experience = ""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await _random_delay(800, 1800)

        # Требуемый опыт (структурированное поле)
        exp_loc = page.locator('[data-qa="vacancy-experience"]').first
        if await exp_loc.count() > 0:
            try:
                experience = (await exp_loc.inner_text()).strip()
            except Exception:
                experience = ""

        # Основной блок описания
        desc = page.locator('[data-qa="vacancy-description"]')
        if await desc.count() > 0:
            text = (await desc.inner_text()).strip()
        else:
            # Запасной вариант
            desc = page.locator(".vacancy-description")
            if await desc.count() > 0:
                text = (await desc.inner_text()).strip()
    except PWTimeout:
        pass
    return text, experience


# ---------- Основные функции ----------

async def is_logged_in(page: Page) -> bool:
    """
    Проверяет авторизацию: залогиненного пользователя HH уводит
    со страницы /account/login. Это надёжнее, чем искать аватар.
    """
    await page.goto(
        "https://hh.ru/account/login", wait_until="domcontentloaded", timeout=20_000
    )
    await asyncio.sleep(1.5)
    if "account/login" not in page.url:
        return True

    await page.goto("https://hh.ru", wait_until="domcontentloaded", timeout=20_000)
    await asyncio.sleep(1)
    marker = page.locator(
        '[data-qa="account-icon"], [data-qa="mainmenu_applicantProfile"], '
        '[data-qa="mainmenu-myResumes"]'
    ).first
    return await marker.count() > 0


# ---------- Импорт cookies (обход робот-проверки) ----------

def _convert_cookies(raw_cookies: list[dict]) -> list[dict]:
    """Конвертирует cookies (формат Cookie-Editor и подобных) в Playwright storage_state."""
    out: list[dict] = []
    for c in raw_cookies:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue

        domain = c.get("domain") or ".hh.ru"
        path = c.get("path") or "/"

        exp = c.get("expires", c.get("expirationDate"))
        if exp is None or c.get("session"):
            expires = -1
        else:
            try:
                expires = int(float(exp))
            except (TypeError, ValueError):
                expires = -1

        ss_raw = str(c.get("sameSite") or "").lower()
        if ss_raw in ("no_restriction", "none"):
            same_site = "None"
        elif ss_raw == "strict":
            same_site = "Strict"
        else:
            same_site = "Lax"

        secure = bool(c.get("secure", False))
        if same_site == "None":
            secure = True

        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "expires": expires,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": secure,
            "sameSite": same_site,
        })
    return out


def parse_cookies_payload(data: Union[str, bytes, list, dict]) -> list[dict]:
    """Разбирает разные форматы экспорта cookies."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        data = json.loads(data)

    if isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            return _convert_cookies(data["cookies"])
        return _convert_cookies(
            [{"name": k, "value": v, "domain": ".hh.ru", "path": "/"} for k, v in data.items()]
        )
    if isinstance(data, list):
        return _convert_cookies(data)
    raise ValueError("Неизвестный формат файла cookies")


async def import_session_from_cookies(payload: Union[str, bytes, list, dict]) -> bool:
    """
    Сохраняет cookies как сессию HH.ru и проверяет, что вход распознан.
    Возвращает True, если HH.ru считает нас авторизованными.
    """
    cookies = parse_cookies_payload(payload)
    hh_cookies = [c for c in cookies if "hh.ru" in c["domain"] or "headhunter" in c["domain"]]
    cookies = hh_cookies or cookies
    if not cookies:
        raise ValueError("В файле не найдено cookies для hh.ru")

    storage_state = {"cookies": cookies, "origins": []}

    async with BROWSER_LOCK:
        # Пишем cookies во временный файл, чтобы не затирать рабочую сессию,
        # пока вход не подтверждён.
        target = Path(SESSION_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(storage_state, f, ensure_ascii=False)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            context = await browser.new_context(
                **_CONTEXT_KWARGS, storage_state=tmp_path
            )
            await context.add_init_script(_STEALTH_SCRIPT)
            page = await context.new_page()
            try:
                ok = await is_logged_in(page)
                if ok:
                    await save_session(context)
            finally:
                await browser.close()

        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return ok


def _clean_resume_title(text: str) -> str:
    """Из текста карточки резюме выбирает название должности."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    skip = (
        "постоянная работа", "временная", "уровень дохода", "на месте",
        "удал", "частичная", "проектная", "стажировка", "·",
    )
    for ln in lines:
        low = ln.lower()
        if any(s in low for s in skip):
            continue
        return ln
    return lines[0] if lines else "Резюме"


def _normalize_ru_phone(phone: str) -> str:
    """10 цифр национального номера для РФ."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) >= 11:
        return digits[1:11]
    return digits[-10:] if len(digits) >= 10 else digits


async def _otp_field_visible(page: Page) -> bool:
    otp = page.locator(
        '[data-qa="magritte-pincode-input-field"], '
        '[data-qa="account-login-otp-code"], '
        'input[name="otp"]'
    ).first
    return await otp.count() > 0


async def _open_phone_login_form(page: Page) -> None:
    """Открывает форму ввода телефона (новый многошаговый UI HH.ru)."""
    await page.goto("https://hh.ru/account/login", wait_until="domcontentloaded", timeout=20_000)
    await asyncio.sleep(1.5)

    legacy = page.locator('[data-qa="account-login-input"]').first
    national = page.locator('[data-qa="magritte-phone-input-national-number-input"]').first
    if await legacy.count() == 0 and await national.count() == 0:
        submit = page.locator('[data-qa="submit-button"]').first
        if await submit.count() > 0:
            await submit.click()
            await asyncio.sleep(2)


async def login_has_otp_form(page: Page) -> bool:
    """Проверяет, что на странице форма ввода SMS-кода."""
    return await _otp_field_visible(page)


async def login_step1_enter_phone(page: Page, phone: str) -> bool:
    """
    Шаг 1 логина: вводим телефон на странице входа.
    Возвращает True если появилась форма ввода SMS-кода.
    """
    await _open_phone_login_form(page)

    legacy = page.locator('[data-qa="account-login-input"]').first
    if await legacy.count() > 0:
        await legacy.fill(phone)
    else:
        national_input = page.locator('[data-qa="magritte-phone-input-national-number-input"]').first
        if await national_input.count() == 0:
            log.warning("Поле телефона не найдено на странице логина")
            return False
        await national_input.fill(_normalize_ru_phone(phone))

    await _random_delay(500, 1000)

    submit = page.locator('[data-qa="account-login-submit"], [data-qa="submit-button"]').first
    if await submit.count() == 0:
        return False
    await submit.click()
    await asyncio.sleep(3)

    if await _otp_field_visible(page):
        return True

    err = page.locator('[data-qa="form-helper-error"]').first
    if await err.count() > 0:
        log.warning("Ошибка логина HH.ru: %s", (await err.inner_text()).strip())
    return False


async def _wait_login_complete(page: Page, timeout_sec: int = 20) -> bool:
    """Ждёт, пока HH уведёт со страницы логина (успешная авторизация)."""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        url = page.url
        if "account/login" not in url:
            return True
        err = page.locator('[data-qa="form-helper-error"], [data-qa="account-login-error"]').first
        if await err.count() > 0:
            txt = (await err.inner_text()).strip()
            if txt:
                log.warning("HH OTP ошибка: %s", txt)
                return False
        await asyncio.sleep(1)
    return "account/login" not in page.url


async def login_step2_enter_otp(page: Page, code: str) -> bool:
    """
    Шаг 2 логина: вводим OTP-код из SMS.
    Поле HH (Magritte pincode) автоотправляется при вводе всех цифр —
    поэтому вводим посимвольно реальными нажатиями клавиш.
    """
    digits = re.sub(r"\D", "", code)

    cells = page.locator('[data-qa="magritte-pincode-input-field"]')
    cell_count = await cells.count()

    if cell_count > 1:
        # Несколько ячеек: по одной цифре в каждую
        for i, ch in enumerate(digits[:cell_count]):
            await cells.nth(i).click()
            await page.keyboard.type(ch, delay=random.randint(80, 180))
            await asyncio.sleep(random.uniform(0.1, 0.3))
    else:
        target = cells.first
        if await target.count() == 0:
            target = page.locator(
                '[data-qa="account-login-otp-code"], input[name="otp"]'
            ).first
        if await target.count() == 0:
            log.warning("Поле ввода кода не найдено")
            return False
        await target.click()
        await target.fill("")
        for ch in digits:
            await page.keyboard.type(ch, delay=random.randint(80, 180))
            await asyncio.sleep(random.uniform(0.08, 0.22))

    await asyncio.sleep(1.5)

    # На некоторых вариантах есть кнопка подтверждения
    submit = page.locator(
        '[data-qa="account-login-submit"], [data-qa="submit-button"]'
    ).first
    if await submit.count() > 0:
        try:
            if await submit.is_enabled():
                await submit.click()
        except Exception:
            pass

    return await _wait_login_complete(page)


async def search_vacancies(query: str, limit: int = MAX_VACANCIES) -> list[VacancyData]:
    """
    Главная функция: ищет вакансии по запросу и возвращает список с полным текстом.
    Использует сохранённую сессию из SESSION_FILE если есть.
    """
    import config as cfg

    region = await storage.get_setting("hh_region", cfg.HH_REGION)
    period = await storage.get_setting("hh_search_period", str(cfg.HH_SEARCH_PERIOD))
    max_from_settings = await storage.get_setting("max_vacancies", str(limit))
    hh_schedule = await storage.get_setting("hh_schedule", "")
    salary_from = await storage.get_setting("salary_from", "0")
    only_with_salary = await storage.get_setting("only_with_salary", "false")
    try:
        limit = min(limit, int(max_from_settings))
    except ValueError:
        pass

    results: list[VacancyData] = []

    async with BROWSER_LOCK, async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        context: BrowserContext = await browser.new_context(**_context_kwargs())
        # Прячем признаки автоматизации
        await context.add_init_script(_STEALTH_SCRIPT)
        page: Page = await context.new_page()

        # Поиск: сортировка по дате, только свежие (за сутки)
        encoded_query = query.replace(" ", "+")
        search_url = (
            f"https://hh.ru/search/vacancy"
            f"?text={encoded_query}"
            f"&area={region}"
            f"&order_by=publication_time"
            f"&search_period={period}"
            f"&per_page=50"
        )
        if hh_schedule:
            search_url += f"&schedule={hh_schedule}"
        try:
            sal = int(salary_from)
            if sal > 0:
                search_url += f"&salary={sal}&currency=RUR"
        except (ValueError, TypeError):
            pass
        if only_with_salary.lower() in ("true", "on", "1", "yes"):
            search_url += "&only_with_salary=true"

        await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        # Если редирект на логин — сессия истекла
        if "account/login" in page.url:
            await browser.close()
            raise RuntimeError("SESSION_EXPIRED")

        await _scroll_to_load_all(page)

        # --- Парсим карточки ---
        cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
        total = await cards.count()

        for i in range(min(total, limit)):
            card = cards.nth(i)

            title_loc = card.locator('[data-qa="serp-item__title-text"]').first
            if await title_loc.count() == 0:
                continue
            title = (await title_loc.inner_text()).strip()

            href_loc = card.locator('a[data-qa="serp-item__title"]').first
            href = await href_loc.get_attribute("href") or ""
            m = re.search(r"/vacancy/(\d+)", href)
            if not m:
                continue
            vacancy_id = m.group(1)
            url = f"https://hh.ru/vacancy/{vacancy_id}"

            company_loc = card.locator('[data-qa="vacancy-serp__vacancy-employer"]').first
            company = (await company_loc.inner_text()).strip() if await company_loc.count() > 0 else "Не указана"

            # Зарплата больше не имеет своего data-qa — берём строку с ₽ из текста карточки
            salary = ""
            salary_loc = card.locator('[data-qa="vacancy-serp__vacancy-compensation"]').first
            if await salary_loc.count() > 0:
                salary = (await salary_loc.inner_text()).strip()
            else:
                card_text = await card.inner_text()
                for line in card_text.splitlines():
                    line = line.strip()
                    if "₽" in line or re.search(r"\bруб", line, re.IGNORECASE):
                        salary = line
                        break

            results.append(
                VacancyData(id=vacancy_id, title=title, company=company, salary=salary, url=url)
            )

        # --- Забираем полный текст и требуемый опыт для каждой вакансии ---
        for v in results:
            await _random_delay(2000, 4500)   # Пауза между запросами — важно!
            v.full_text, v.experience = await _get_full_text(page, v.url)

        # Сохраняем сессию
        await save_session(context)
        await browser.close()

    return results


async def _new_browser_context(use_session: bool = True):
    """Создаёт Playwright browser + context с сохранённой сессией."""
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
    context = await browser.new_context(**_context_kwargs(use_session))
    await context.add_init_script(_STEALTH_SCRIPT)
    return p, browser, context


async def get_my_resumes() -> list[ResumeInfo]:
    """Открывает https://hh.ru/resume/mine и собирает список резюме."""
    results: list[ResumeInfo] = []
    async with BROWSER_LOCK:
        p, browser, context = await _new_browser_context()
        page = await context.new_page()
        try:
            # Актуальный URL списка резюме (2026). Старый /resume/mine выдаёт ошибку.
            await page.goto(
                "https://hh.ru/applicant/resumes",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await asyncio.sleep(3)

            if "account/login" in page.url:
                raise RuntimeError("SESSION_EXPIRED")

            # Подгружаем все резюме (кнопка "Показать ещё")
            for _ in range(5):
                more = page.locator('[data-qa="compact-resume-show-more"]').first
                if await more.count() == 0:
                    break
                try:
                    await more.click()
                    await asyncio.sleep(1.5)
                except Exception:
                    break

            seen: set[str] = set()
            links = page.locator('a[data-qa^="resume-card-link-"]')
            count = await links.count()
            for i in range(count):
                link = links.nth(i)
                href = await link.get_attribute("href") or ""
                m = re.search(r"/resume/([a-f0-9]+)", href)
                if not m:
                    continue
                resume_id = m.group(1)
                if resume_id in seen:
                    continue
                seen.add(resume_id)

                title = _clean_resume_title(await link.inner_text())
                results.append(ResumeInfo(id=resume_id, title=title))

            await save_session(context)
        finally:
            await browser.close()
            await p.stop()
    return results


async def parse_resume_full(resume_id: str) -> ResumeFullData:
    """Парсит полную страницу резюме."""
    async with BROWSER_LOCK:
        p, browser, context = await _new_browser_context()
        page = await context.new_page()
        try:
            url = f"https://hh.ru/resume/{resume_id}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await _random_delay(1000, 2000)

            if "account/login" in page.url:
                raise RuntimeError("SESSION_EXPIRED")

            title_loc = page.locator('[data-qa="resume-block-title-position"]').first
            if await title_loc.count() == 0:
                title_loc = page.locator("h1, h2").first
            title = (await title_loc.inner_text()).strip() if await title_loc.count() > 0 else "Резюме"

            # Навыки: вытаскиваем теги, если они есть (селекторы 2026 могут отличаться)
            skills: list[str] = []
            for sel in (
                '[data-qa="skills-table"] [data-qa="bloko-tag__text"]',
                '[data-qa="resume-block-skill"] [data-qa="bloko-tag__text"]',
                '[data-qa="bloko-tag__text"]',
                '[data-qa="resume-skill-item"]',
            ):
                tags = page.locator(sel)
                cnt = await tags.count()
                if cnt:
                    for j in range(cnt):
                        txt = (await tags.nth(j).inner_text()).strip()
                        if txt and txt not in skills:
                            skills.append(txt)
                    break

            # raw_text: берём весь видимый текст и срезаем шапку/подвал сайта
            body_text = (await page.locator("body").inner_text()).strip()
            idx = body_text.find(title)
            if idx > 0:
                body_text = body_text[idx:]
            for marker in (
                "Создать резюме на основе", "Похожие резюме", "О компании",
                "Наши вакансии", "Реклама на сайте", "Требования к ПО",
            ):
                j = body_text.find(marker)
                if j > 500:
                    body_text = body_text[:j]
                    break
            raw_text = body_text.strip()

            if skills:
                raw_text = raw_text + "\n\nНавыки: " + ", ".join(skills)

            await save_session(context)

            return ResumeFullData(
                id=resume_id,
                title=title,
                skills=skills,
                raw_text=raw_text,
                specialization="",
            )
        finally:
            await browser.close()
            await p.stop()


async def check_responses() -> list[str]:
    """
    Парсит переговоры на HH.ru и возвращает vacancy_id с новыми ответами.
    """
    responded: list[str] = []
    async with BROWSER_LOCK:
        p, browser, context = await _new_browser_context()
        page = await context.new_page()
        try:
            await page.goto(
                "https://hh.ru/applicant/negotiations",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await asyncio.sleep(2)

            if "account/login" in page.url:
                return []

            items = page.locator('[data-qa="negotiations-list-item"]')
            count = await items.count()
            for i in range(count):
                item = items.nth(i)
                link = item.locator('a[href*="/vacancy/"]').first
                if await link.count() == 0:
                    continue
                href = await link.get_attribute("href") or ""
                m = re.search(r"/vacancy/(\d+)", href)
                if not m:
                    continue
                vacancy_id = m.group(1)

                status_text = ""
                status_loc = item.locator('[data-qa="negotiations-item-status"]').first
                if await status_loc.count() > 0:
                    status_text = (await status_loc.inner_text()).lower()

                if any(w in status_text for w in ("приглаш", "ответ", "просмотр", "интервью")):
                    responded.append(vacancy_id)

            await save_session(context)
        finally:
            await browser.close()
            await p.stop()
    return responded
