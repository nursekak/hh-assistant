"""Analytics repository."""

from __future__ import annotations

import storage


class AnalyticsRepository:
    """Доступ к аналитическим выборкам."""

    async def funnel(self) -> dict[str, int]:
        return await storage.get_analytics_funnel()

    async def daily(self, days: int = 14) -> list[dict]:
        return await storage.get_analytics_daily(days)

    async def match_histogram(self, buckets: int = 10) -> list[dict]:
        return await storage.get_analytics_match_histogram(buckets)

    async def missing_skills(
        self,
        limit: int = 15,
        resume_id: str | None = None,
    ) -> list[dict]:
        return await storage.get_analytics_missing_skills(limit=limit, resume_id=resume_id)

    async def company_conversion(self, limit: int = 20) -> list[dict]:
        return await storage.get_analytics_company_conversion(limit)
