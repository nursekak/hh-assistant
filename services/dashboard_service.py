"""Business logic for web dashboard."""

from __future__ import annotations

from pathlib import Path

import config
import llm
from repositories import (
    ResumeRepository,
    ScanJobRepository,
    SettingsRepository,
    VacancyRepository,
)


class DashboardService:
    """Собирает данные главной страницы без прямого обращения роутера к storage."""

    def __init__(
        self,
        settings_repo: SettingsRepository | None = None,
        resume_repo: ResumeRepository | None = None,
        vacancy_repo: VacancyRepository | None = None,
        scan_job_repo: ScanJobRepository | None = None,
    ) -> None:
        self.settings_repo = settings_repo or SettingsRepository()
        self.resume_repo = resume_repo or ResumeRepository()
        self.vacancy_repo = vacancy_repo or VacancyRepository()
        self.scan_job_repo = scan_job_repo or ScanJobRepository()

    async def is_scan_running(self) -> bool:
        return await self.scan_job_repo.is_running()

    async def get_dashboard_context(self) -> dict:
        query = await self.settings_repo.get("query", config.DEFAULT_QUERY)
        threshold = await self.settings_repo.get_match_threshold(config.MIN_MATCH_THRESHOLD)
        model = await self.settings_repo.get("ollama_model", config.OLLAMA_MODEL)

        return {
            "query": query,
            "threshold_pct": int(threshold * 100),
            "active_resume": await self.resume_repo.get_active(),
            "ollama_ok": await llm.check_ollama(model),
            "session_ok": Path(config.SESSION_FILE).exists(),
            "stats": await self.vacancy_repo.get_stats(),
            "recent": await self.vacancy_repo.list_recent(5),
            "applied_today": await self.vacancy_repo.get_applied_count_since(1),
            "applied_week": await self.vacancy_repo.get_applied_count_since(7),
        }

    async def list_vacancies(self, limit: int = 200) -> list[dict]:
        return await self.vacancy_repo.list_all(limit)

    async def get_bot_status(self) -> dict:
        """Контекст для команды /status в Telegram."""
        model = await self.settings_repo.get("ollama_model", config.OLLAMA_MODEL)
        threshold = await self.settings_repo.get_match_threshold(config.MIN_MATCH_THRESHOLD)
        return {
            "query": await self.settings_repo.get("query", config.DEFAULT_QUERY),
            "ollama_ok": await llm.check_ollama(model),
            "ollama_model": model,
            "session_ok": Path(config.SESSION_FILE).exists(),
            "stats": await self.vacancy_repo.get_stats(),
            "active_resume": await self.resume_repo.get_active(),
            "threshold_pct": int(threshold * 100),
        }
