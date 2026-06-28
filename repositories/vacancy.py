"""Vacancy repository."""

from __future__ import annotations

from typing import Optional

import storage


class VacancyRepository:
    """Доступ к вакансиям, статусам и статистическим счетчикам."""

    async def is_seen(self, vacancy_id: str) -> bool:
        return await storage.is_seen(vacancy_id)

    async def get(self, vacancy_id: str) -> Optional[dict]:
        return await storage.get_vacancy(vacancy_id)

    async def list_recent(self, limit: int = 5) -> list[dict]:
        return await storage.get_recent_vacancies(limit)

    async def list_all(self, limit: int = 200) -> list[dict]:
        return await storage.get_all_vacancies(limit)

    async def get_stats(self) -> dict:
        return await storage.get_stats()

    async def get_applied_count_since(self, days: int) -> int:
        return await storage.get_applied_count_since(days)

    async def reset_statistics(self) -> int:
        return await storage.reset_statistics()

    async def update_status(self, vacancy_id: str, status: str) -> None:
        await storage.update_status(vacancy_id, status)

    async def mark_response_received(self, vacancy_id: str) -> None:
        await storage.mark_response_received(vacancy_id)

    async def set_cover_letter(self, vacancy_id: str, text: str) -> None:
        await storage.set_cover_letter(vacancy_id, text)

    async def save(
        self,
        vacancy_id: str,
        title: str,
        company: str,
        url: str,
        salary: str = "",
        summary: str = "",
        status: str = "new",
        match_score: float = 0.0,
        matched_skills: list[str] | None = None,
        missing_skills: list[str] | None = None,
        extra_skills: list[str] | None = None,
        profile_json: str = "",
        cover_letter: str = "",
        scan_query: str = "",
        resume_id: str = "",
    ) -> None:
        await storage.save_vacancy(
            vacancy_id=vacancy_id,
            title=title,
            company=company,
            url=url,
            salary=salary,
            summary=summary,
            status=status,
            match_score=match_score,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            extra_skills=extra_skills,
            profile_json=profile_json,
            cover_letter=cover_letter,
            scan_query=scan_query,
            resume_id=resume_id,
        )
