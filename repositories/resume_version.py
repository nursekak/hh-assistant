"""Resume version repository (иммутабельная история версий резюме)."""

from __future__ import annotations

from typing import Optional

import storage


class ResumeVersionRepository:
    """Доступ к истории версий резюме и откату."""

    async def list(self, resume_id: str) -> list[dict]:
        return await storage.get_resume_versions(resume_id)

    async def count(self, resume_id: str) -> int:
        return len(await storage.get_resume_versions(resume_id))

    async def get(self, resume_id: str, version: int) -> Optional[dict]:
        return await storage.get_resume_version(resume_id, version)

    async def restore(self, resume_id: str, version: int) -> bool:
        return await storage.restore_resume_version(resume_id, version)
