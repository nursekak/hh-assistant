"""Business logic for vacancy scanning pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import config
import experience
import extractor
import llm
import matcher
import sanitizer
import scan_debug
import scan_phases
import scraper
import tg_client
from extractor import ResumeProfile, VacancyProfile
from repositories import ResumeRepository, ScanJobRepository, SettingsRepository, VacancyRepository
from services.telegram_ui import build_vacancy_keyboard, format_vacancy_message

log = logging.getLogger(__name__)

SendMessageFn = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class ScanNotifier:
    """Адаптер отправки сообщений в Telegram (инжектится из bot.py/worker.py)."""

    send_message: SendMessageFn
    user_id: int

    async def notify(self, text: str, **kwargs: Any) -> None:
        await self.send_message(self.user_id, text, **kwargs)


class ScanService:
    """Оркестратор пайплайна сканирования вакансий.

    run()      — публичная точка входа (dedup + Job Manager + retry-safe).
    execute()  — собственно пайплайн для уже созданной задачи scan_jobs.
    """

    def __init__(
        self,
        notifier: ScanNotifier | None = None,
        settings_repo: SettingsRepository | None = None,
        resume_repo: ResumeRepository | None = None,
        vacancy_repo: VacancyRepository | None = None,
        scan_job_repo: ScanJobRepository | None = None,
    ) -> None:
        self.notifier = notifier
        self.settings_repo = settings_repo or SettingsRepository()
        self.resume_repo = resume_repo or ResumeRepository()
        self.vacancy_repo = vacancy_repo or VacancyRepository()
        self.scan_job_repo = scan_job_repo or ScanJobRepository()

    async def get_ollama_model(self) -> str:
        return await self.settings_repo.get("ollama_model", config.OLLAMA_MODEL)

    async def run(self, attempt: int = 1, worker_id: str = "") -> None:
        """Создаёт задачу и выполняет её. No-op, если скан уже идёт.

        Бросает исключение наружу при неожиданной ошибке (для retry воркера),
        предварительно пометив задачу как error.
        """
        query = await self.settings_repo.get("query", config.DEFAULT_QUERY)
        job_id = await self.scan_job_repo.begin(query)
        if job_id is None:
            log.info("Скан уже выполняется — пропускаю повторный запуск")
            return

        await self.scan_job_repo.record_meta(job_id, attempt=attempt, worker_id=worker_id)
        log.info("Скан запущен: query=%r job_id=%s attempt=%s", query, job_id, attempt)

        try:
            await self.execute(job_id, query)
        except Exception:
            log.exception("Непредвиденная ошибка скана (job_id=%s)", job_id)
            await self.scan_job_repo.update(job_id, error="Внутренняя ошибка")
            await self.scan_job_repo.finish(job_id, scan_phases.ERROR, "Внутренняя ошибка")
            raise

    async def execute(self, job_id: int, query: str) -> None:
        """Пайплайн для уже созданной задачи job_id."""
        await self.scan_job_repo.set_phase(
            job_id, scan_phases.SEARCHING, scan_phases.label(scan_phases.SEARCHING)
        )

        hh_available = Path(config.SESSION_FILE).exists()
        tg_available = await self._tg_scan_available()

        if not hh_available and not tg_available:
            await self._fail_preflight(
                job_id,
                "Нет доступных источников",
                "⚠️ Нет ни сессии HH.ru (/login), ни Telegram-каналов "
                "(включите TG в настройках и выполните tg_login.py).",
            )
            return

        model = await self.get_ollama_model()
        if not await llm.check_ollama(model):
            await self._fail_preflight(
                job_id,
                "LLM недоступна",
                f"⚠️ Ollama недоступна. Убедись, что она запущена:\n"
                f"<code>ollama serve</code>\n"
                f"<code>ollama pull {model}</code>",
                parse_mode="HTML",
            )
            return

        collectors: list[Awaitable[list[scraper.VacancyData]]] = []
        labels: list[str] = []
        if hh_available:
            labels.append("HH")
            collectors.append(self._collect_hh(job_id, query))
        if tg_available:
            labels.append("TG")
            collectors.append(self._collect_tg(job_id))

        results = await asyncio.gather(*collectors, return_exceptions=True)
        vacancies: list[scraper.VacancyData] = []
        sources_ok = 0
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                log.exception("Ошибка источника %s", label, exc_info=result)
                await self.scan_job_repo.log(job_id, f"❌ [{label}] {result}")
                if label == "HH" and self.notifier and "SESSION_EXPIRED" in str(result):
                    await self.notifier.notify("🔑 Сессия HH.ru истекла. Запусти /login заново.")
            else:
                sources_ok += 1
                vacancies.extend(result)
                await self.scan_job_repo.log(
                    job_id, f"[{label}] Собрано вакансий: {len(result)}"
                )

        if sources_ok == 0:
            await self.scan_job_repo.update(job_id, error="Все источники недоступны")
            await self.scan_job_repo.finish(job_id, scan_phases.ERROR, "Ошибка парсера")
            if self.notifier:
                await self.notifier.notify("❌ Не удалось собрать вакансии ни из одного источника.")
            return

        await self._process_vacancies(job_id, vacancies, query, model)

    async def _collect_hh(self, job_id: int, query: str) -> list[scraper.VacancyData]:
        recorder = scan_debug.ScanDebugRecorder(job_id, query)
        await self.scan_job_repo.log(job_id, "[HH] Открываю HH.ru и собираю вакансии…")
        vacancies = await scraper.search_vacancies(
            query, limit=config.MAX_VACANCIES, debug=recorder
        )
        if recorder.enabled and recorder.pages:
            tot = recorder.summary()
            await self.scan_job_repo.log(
                job_id,
                f"[HH] 🖼 Визуальный отчёт: {tot['cards_total']} карточек "
                f"на {tot['pages']} стр. (новых {tot['new_total']}, "
                f"в базе {tot['seen_total']}) — раздел «Парсинг»",
            )
        return vacancies

    async def _collect_tg(self, job_id: int) -> list[scraper.VacancyData]:
        folder = await self.settings_repo.get(
            "tg_channels_folder", config.TG_CHANNELS_FOLDER
        )
        lookback = await self._get_int_setting(
            "tg_lookback_hours", config.TG_LOOKBACK_HOURS
        )
        max_per = await self._get_int_setting(
            "tg_max_messages_per_channel", config.TG_MAX_MESSAGES_PER_CHANNEL
        )
        await self.scan_job_repo.log(
            job_id,
            f"[TG] Читаю каналы из папки «{folder}» (lookback={lookback}ч)…",
        )
        return await tg_client.fetch_new_messages(
            folder_name=folder,
            lookback_hours=lookback,
            max_per_channel=max_per,
            is_seen=self.vacancy_repo.is_seen,
        )

    async def _tg_scan_available(self) -> bool:
        enabled = await self._get_bool_setting("tg_scan_enabled", config.TG_SCAN_ENABLED)
        if not enabled:
            return False
        if not tg_client.is_configured():
            return False
        folder = await self.settings_repo.get(
            "tg_channels_folder", config.TG_CHANNELS_FOLDER
        )
        return bool(folder.strip())

    @staticmethod
    def _source_tag(vacancy: scraper.VacancyData) -> str:
        return "[TG]" if vacancy.source == "tg" else "[HH]"

    async def _get_bool_setting(self, key: str, default: bool) -> bool:
        raw = await self.settings_repo.get(key, "")
        if not raw:
            return default
        return raw.lower() in ("1", "true", "yes", "on")

    async def _get_int_setting(self, key: str, default: int) -> int:
        try:
            return int(await self.settings_repo.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    async def _fail_preflight(
        self,
        job_id: int,
        error: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        await self.scan_job_repo.update(job_id, error=error)
        await self.scan_job_repo.log(job_id, f"⚠️ {error}")
        await self.scan_job_repo.finish(job_id, scan_phases.ERROR, error)
        if self.notifier:
            await self.notifier.notify(message, **kwargs)

    async def _process_vacancies(
        self,
        job_id: int,
        vacancies: list[scraper.VacancyData],
        query: str,
        model: str,
    ) -> None:
        new_count = 0
        below_threshold = 0
        exp_skipped = 0
        processed = 0
        threshold = await self.settings_repo.get_match_threshold(config.MIN_MATCH_THRESHOLD)
        notify_below = await self._notify_below_threshold_enabled()

        exp_filter_on = await self._experience_filter_enabled()
        exp_tolerance = await self._experience_tolerance()

        active_resume = await self.resume_repo.get_active()
        resume_years: float | None = None
        if active_resume:
            resume_years = experience.parse_resume_years(active_resume.get("raw_text", ""))
        if exp_filter_on and resume_years is not None:
            await self.scan_job_repo.log(
                job_id,
                f"🧮 Стаж по резюме: ~{resume_years} лет (фильтр опыта включён)",
            )

        resume_profile = await self._ensure_resume_profile(active_resume, model)

        await self.scan_job_repo.update(job_id, total=len(vacancies))
        await self.scan_job_repo.set_phase(
            job_id, scan_phases.MATCHING, scan_phases.label(scan_phases.MATCHING)
        )

        for vacancy in vacancies:
            processed += 1
            tag = self._source_tag(vacancy)
            await self.scan_job_repo.update(
                job_id,
                processed=processed,
                current_title=vacancy.title,
                current_company=vacancy.company,
            )

            if await self.vacancy_repo.is_seen(vacancy.id):
                await self.scan_job_repo.log(job_id, f"{tag} ↺ Уже видели: {vacancy.title}")
                continue

            if exp_filter_on and resume_years is not None:
                req_years = experience.parse_required_years(vacancy.experience)
                if req_years is None and vacancy.full_text:
                    req_years = experience.parse_required_years(vacancy.full_text)
                if not experience.is_experience_ok(req_years, resume_years, exp_tolerance):
                    exp_skipped += 1
                    await self.scan_job_repo.update(
                        job_id,
                        skipped_count=below_threshold + exp_skipped,
                    )
                    await self.scan_job_repo.log(
                        job_id,
                        f"{tag} ⏭ Опыт {vacancy.experience or req_years} > резюме "
                        f"(~{resume_years}л) — пропуск: {vacancy.title}",
                    )
                    continue

            match_result = None
            vac_profile = VacancyProfile()
            if active_resume:
                san = sanitizer.sanitize(vacancy.full_text or vacancy.title)
                vac_text = san.text if san.text else vacancy.title
                await self.scan_job_repo.log(job_id, f"{tag} 🔍 Парсинг требований: {vacancy.title}")
                vac_profile = await extractor.extract_vacancy_requirements(
                    vacancy.title, vacancy.company, vac_text, model=model,
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
                    await self.scan_job_repo.update(job_id, skipped_count=below_threshold)
                    resume_id = active_resume["id"] if active_resume else ""

                    # Порог отправки в Telegram: при выключенной опции вакансию
                    # ниже порога сохраняем в БД, но карточку в Telegram не шлём.
                    if not notify_below:
                        await self.scan_job_repo.log(
                            job_id,
                            f"{tag} 🚫 {match_result.score_pct}% — ниже порога, без Telegram: {vacancy.title}",
                        )
                        await self._save_and_notify(
                            vacancy, match_result, query, vac_profile, model, resume_id,
                            notify=False,
                        )
                        continue

                    await self.scan_job_repo.log(
                        job_id,
                        f"{tag} ⚠️ {match_result.score_pct}% — ниже порога: {vacancy.title}",
                    )

            new_count += 1
            await self.scan_job_repo.update(job_id, new_count=new_count)
            pct = f"{match_result.score_pct}% — " if match_result else ""
            await self.scan_job_repo.log(
                job_id,
                f"{tag} ✅ {pct}отправляю: {vacancy.title} ({vacancy.company})",
            )

            resume_id = active_resume["id"] if active_resume else ""
            await self._save_and_notify(
                vacancy, match_result, query, vac_profile, model, resume_id,
            )
            await asyncio.sleep(2)

        await self.scan_job_repo.set_phase(
            job_id, scan_phases.FINALIZING, scan_phases.label(scan_phases.FINALIZING)
        )
        await self.scan_job_repo.log(
            job_id,
            f"Готово. Новых: {new_count}, ниже порога: {below_threshold}, "
            f"пропущено по опыту: {exp_skipped}",
        )
        await self.scan_job_repo.finish(
            job_id, scan_phases.DONE, scan_phases.label(scan_phases.DONE)
        )

        if new_count == 0 and self.notifier:
            msg = "😴 Новых вакансий не найдено."
            if below_threshold and not notify_below:
                msg += f"\n🚫 Ниже порога (не отправлены, см. веб): {below_threshold}"
            if exp_skipped:
                msg += f"\n⏭ Отсеяно по требуемому опыту: {exp_skipped}"
            await self.notifier.notify(msg)
        else:
            log.info(
                "Отправлено карточек: %d, ниже порога: %d, пропущено по опыту: %d",
                new_count, below_threshold, exp_skipped,
            )

    async def _ensure_resume_profile(
        self,
        active_resume: dict | None,
        model: str,
    ) -> ResumeProfile:
        if not active_resume:
            return ResumeProfile()

        resume_profile = ResumeProfile.from_json(active_resume.get("profile_json") or "")
        if resume_profile.all_skills() or not active_resume.get("raw_text"):
            return resume_profile

        resume_profile = await extractor.extract_resume_profile(
            active_resume["raw_text"], model=model,
        )
        await self.resume_repo.save(
            active_resume["id"],
            active_resume["title"],
            raw_text=active_resume["raw_text"],
            keywords=resume_profile.all_skills(),
            profile_json=resume_profile.to_json(),
        )
        return resume_profile

    async def _save_and_notify(
        self,
        vacancy: scraper.VacancyData,
        match_result: matcher.MatchResult | None,
        scan_query: str,
        vac_profile: VacancyProfile,
        model: str,
        resume_id: str,
        notify: bool = True,
    ) -> None:
        san = sanitizer.sanitize(vacancy.full_text)
        text_for_llm = san.text if san.text else vacancy.title

        # Когда карточка не отправляется (ниже порога при выключенной опции),
        # дорогой LLM-разбор не нужен — просто сохраняем факт вакансии в БД.
        summary = ""
        if notify:
            summary = await llm.analyze_vacancy(
                title=vacancy.title,
                company=vacancy.company,
                salary=vacancy.salary,
                text=text_for_llm,
                model=model or config.OLLAMA_MODEL,
            )

        status = "below_threshold" if match_result and match_result.verdict == "SKIP" else "shown"

        await self.vacancy_repo.save(
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            url=vacancy.url,
            salary=vacancy.salary,
            summary=summary,
            status=status,
            match_score=match_result.score if match_result else 0.0,
            matched_skills=match_result.matched if match_result else [],
            missing_skills=match_result.missing if match_result else [],
            extra_skills=match_result.extra if match_result else [],
            profile_json=vac_profile.to_json() if vac_profile else "",
            scan_query=scan_query,
            resume_id=resume_id,
            source=vacancy.source,
        )

        if not notify or not self.notifier:
            return

        inject_warn = ""
        if san.is_suspicious:
            inject_warn = (
                "\n\n⚠️ <b>Внимание:</b> в тексте вакансии обнаружены "
                f"подозрительные фрагменты ({', '.join(san.found_tags)}). "
                "Они нейтрализованы."
            )

        text = format_vacancy_message(vacancy, summary, match_result, inject_warn)
        kb = build_vacancy_keyboard(vacancy, match_result)

        try:
            await self.notifier.notify(
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.error("Ошибка отправки в Telegram: %s", e)

    async def _notify_below_threshold_enabled(self) -> bool:
        raw = await self.settings_repo.get("notify_below_threshold", "")
        if raw:
            return raw.lower() in ("1", "true", "yes", "on")
        return config.NOTIFY_BELOW_THRESHOLD

    async def _experience_filter_enabled(self) -> bool:
        raw = await self.settings_repo.get("experience_filter", "")
        if raw:
            return raw.lower() in ("1", "true", "yes", "on")
        return config.EXPERIENCE_FILTER_ENABLED

    async def _experience_tolerance(self) -> float:
        try:
            return float(
                await self.settings_repo.get(
                    "experience_tolerance_years",
                    str(config.EXPERIENCE_TOLERANCE_YEARS),
                )
            )
        except (ValueError, TypeError):
            return config.EXPERIENCE_TOLERANCE_YEARS
