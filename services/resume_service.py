"""Business logic for resume management."""

from __future__ import annotations

import extractor
import scraper
from repositories import (
    AnalyticsRepository,
    ResumeRepository,
    ResumeVersionRepository,
    SettingsRepository,
)


class ResumeService:
    """Операции над резюме, включая парсинг HH, LLM-профилирование и версии."""

    def __init__(
        self,
        resume_repo: ResumeRepository | None = None,
        settings_repo: SettingsRepository | None = None,
        analytics_repo: AnalyticsRepository | None = None,
        version_repo: ResumeVersionRepository | None = None,
    ) -> None:
        self.resume_repo = resume_repo or ResumeRepository()
        self.settings_repo = settings_repo or SettingsRepository()
        self.analytics_repo = analytics_repo or AnalyticsRepository()
        self.version_repo = version_repo or ResumeVersionRepository()

    async def list_with_missing_skills(self) -> list[dict]:
        resumes = await self.resume_repo.list()
        for resume in resumes:
            resume["missing_skills"] = await self.analytics_repo.missing_skills(
                limit=20,
                resume_id=resume["id"],
            )
            resume["versions"] = await self.version_repo.list(resume["id"])
            resume["version_count"] = len(resume["versions"])
        return resumes

    async def list_versions(self, resume_id: str) -> list[dict]:
        return await self.version_repo.list(resume_id)

    async def get_version(self, resume_id: str, version: int) -> dict | None:
        return await self.version_repo.get(resume_id, version)

    async def restore_version(self, resume_id: str, version: int) -> bool:
        return await self.version_repo.restore(resume_id, version)

    async def fetch_from_hh(self) -> int:
        hh_resumes = await scraper.get_my_resumes()
        for resume in hh_resumes:
            await self.resume_repo.save(resume.id, resume.title)
        return len(hh_resumes)

    async def activate(self, resume_id: str) -> None:
        await self.resume_repo.set_active(resume_id)
        # После выбора активного резюме сразу стараемся сохранить полный профиль.
        await self.reparse(resume_id)

    async def reparse(self, resume_id: str) -> None:
        model = await self.settings_repo.get("ollama_model") or None
        data = await scraper.parse_resume_full(resume_id)
        profile = await extractor.extract_resume_profile(data.raw_text, model=model)
        for skill in data.skills:
            if skill:
                profile.skills.append(skill.strip().lower())
        await self.resume_repo.save(
            data.id,
            data.title,
            raw_text=data.raw_text,
            keywords=profile.all_skills(),
            profile_json=profile.to_json(),
        )
