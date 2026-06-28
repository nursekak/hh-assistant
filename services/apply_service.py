"""Business logic for vacancy apply flow."""

from __future__ import annotations

import asyncio
import logging

import config
import letter
import scraper
from extractor import ResumeProfile, VacancyProfile
from repositories import ResumeRepository, SettingsRepository, VacancyRepository

log = logging.getLogger(__name__)


class ApplyService:
    """Генерация писем и отправка откликов на HH.ru."""

    def __init__(
        self,
        vacancy_repo: VacancyRepository | None = None,
        resume_repo: ResumeRepository | None = None,
        settings_repo: SettingsRepository | None = None,
    ) -> None:
        self.vacancy_repo = vacancy_repo or VacancyRepository()
        self.resume_repo = resume_repo or ResumeRepository()
        self.settings_repo = settings_repo or SettingsRepository()

    async def generate_cover_letter(self, vacancy_id: str) -> tuple[dict, str] | None:
        vacancy = await self.vacancy_repo.get(vacancy_id)
        if not vacancy:
            return None

        active = await self.resume_repo.get_active()
        vac_profile = VacancyProfile.from_json(vacancy.get("profile_json") or "")
        res_profile = ResumeProfile()
        if active:
            res_profile = ResumeProfile.from_json(active.get("profile_json") or "")

        model = await self.settings_repo.get("ollama_model", config.OLLAMA_MODEL)
        cl_backend = await self.settings_repo.get("cover_letter_backend", "")
        cl_api_key = await self.settings_repo.get("anthropic_api_key", "")

        cover = await letter.generate_cover_letter(
            vac_profile,
            res_profile,
            vacancy.get("matched_skills") or [],
            title=vacancy.get("title", ""),
            company=vacancy.get("company", ""),
            model=model,
            backend=cl_backend,
            api_key=cl_api_key,
        )
        await self.vacancy_repo.set_cover_letter(vacancy_id, cover)
        return vacancy, cover

    async def update_cover_letter(self, vacancy_id: str, text: str) -> None:
        await self.vacancy_repo.set_cover_letter(vacancy_id, text)

    async def skip_vacancy(self, vacancy_id: str) -> None:
        await self.vacancy_repo.update_status(vacancy_id, "skipped")

    async def submit_application(self, vacancy_id: str, with_letter: bool = True) -> bool:
        vacancy = await self.vacancy_repo.get(vacancy_id)
        cover = ""
        if with_letter and vacancy:
            cover = vacancy.get("cover_letter", "") or ""

        ok = await self._playwright_apply(f"https://hh.ru/vacancy/{vacancy_id}", cover)
        if ok:
            await self.vacancy_repo.update_status(vacancy_id, "applied")
        return ok

    async def _playwright_apply(self, vacancy_url: str, cover_letter: str = "") -> bool:
        from playwright.async_api import TimeoutError as PWTimeout

        async with scraper.BROWSER_LOCK:
            p, browser, context = await scraper._new_browser_context()
            page = await context.new_page()

            try:
                await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(2)

                if await page.locator('[data-qa="vacancy-response-link-top-again"]').count() > 0:
                    return True

                btn = page.locator('[data-qa="vacancy-response-link-top"]').first
                if await btn.count() == 0:
                    return False

                await btn.click()
                await asyncio.sleep(3)

                if any(s in page.url for s in ("/applicant/vacancy_response", "questionnaire", "quiz")):
                    return False

                submit = page.locator('[data-qa="vacancy-response-submit-popup"]').first
                try:
                    await submit.wait_for(state="visible", timeout=8_000)
                except PWTimeout:
                    if await self._apply_succeeded(page):
                        await scraper.save_session(context)
                        return True
                    return False

                active = await self.resume_repo.get_active()
                if active:
                    radio = page.locator(
                        f'[data-qa="resume-title-{active["id"]}"], '
                        f'[data-resume-hash="{active["id"]}"]'
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

                if await self._apply_succeeded(page):
                    await scraper.save_session(context)
                    return True
                return False

            except PWTimeout:
                return False
            finally:
                await browser.close()
                await p.stop()

    async def _apply_succeeded(self, page) -> bool:
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
