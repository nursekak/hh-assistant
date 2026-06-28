"""Business logic for analytics pages."""

from __future__ import annotations

from repositories import AnalyticsRepository, ResumeRepository


class AnalyticsService:
    """Сбор аналитического view-model для веб-интерфейса."""

    def __init__(
        self,
        analytics_repo: AnalyticsRepository | None = None,
        resume_repo: ResumeRepository | None = None,
    ) -> None:
        self.analytics_repo = analytics_repo or AnalyticsRepository()
        self.resume_repo = resume_repo or ResumeRepository()

    async def get_dashboard_data(self) -> dict:
        active = await self.resume_repo.get_active()
        missing = await self.analytics_repo.missing_skills(
            resume_id=active["id"] if active else None
        )

        return {
            "funnel": await self.analytics_repo.funnel(),
            "daily": await self.analytics_repo.daily(14),
            "histogram": await self.analytics_repo.match_histogram(),
            "missing": missing,
            "companies": await self.analytics_repo.company_conversion(),
            "missing_resume_title": active["title"] if active else None,
        }
