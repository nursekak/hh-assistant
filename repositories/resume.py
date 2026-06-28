"""Resume repository."""

from __future__ import annotations

from typing import Optional

import storage


class ResumeRepository:
    """Доступ к резюме и активному резюме."""

    async def list(self) -> list[dict]:
        return await storage.get_resumes()

    async def get_active(self) -> Optional[dict]:
        return await storage.get_active_resume()

    async def save(
        self,
        resume_id: str,
        title: str,
        raw_text: str = "",
        keywords: list[str] | None = None,
        profile_json: str = "",
        parsed_at: str | None = None,
    ) -> None:
        await storage.save_resume(
            resume_id=resume_id,
            title=title,
            raw_text=raw_text,
            keywords=keywords,
            profile_json=profile_json,
            parsed_at=parsed_at,
        )

    async def set_active(self, resume_id: str) -> None:
        await storage.set_active_resume(resume_id)
