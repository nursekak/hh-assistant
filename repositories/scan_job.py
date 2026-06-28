"""Repository for scan job persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import storage


class ScanJobRepository:
    """CRUD для scan_jobs — замена in-memory scan_state."""

    IDLE_STATUS: dict[str, Any] = {
        "running": False,
        "phase": "idle",
        "phase_label": "Ожидание",
        "query": "",
        "elapsed": 0.0,
        "total": 0,
        "processed": 0,
        "new_count": 0,
        "skipped_count": 0,
        "current_title": "",
        "current_company": "",
        "error": "",
        "logs": [],
    }

    async def is_running(self) -> bool:
        return await storage.is_scan_running()

    async def get_status(self) -> dict[str, Any]:
        job = await storage.get_running_scan_job()
        if not job:
            job = await storage.get_latest_scan_job()
        if not job:
            return dict(self.IDLE_STATUS)
        return self._to_api_dict(job)

    async def begin(self, query: str, job_type: str = "scan") -> Optional[int]:
        """Создаёт новую задачу. None — если скан уже выполняется."""
        if await self.is_running():
            return None
        try:
            return await storage.create_scan_job(query, job_type)
        except RuntimeError:
            return None

    async def record_meta(
        self,
        job_id: int,
        attempt: int = 1,
        worker_id: str = "",
    ) -> None:
        await storage.update_scan_job(job_id, attempts=attempt, worker_id=worker_id)

    async def log(self, job_id: int, message: str) -> None:
        await storage.append_scan_log(job_id, message)

    async def set_phase(self, job_id: int, phase: str, label: str) -> None:
        await storage.update_scan_job(job_id, phase=phase, phase_label=label)

    async def update(self, job_id: int, **fields: Any) -> None:
        await storage.update_scan_job(job_id, **fields)

    async def finish(self, job_id: int, phase: str = "done", label: str = "Готово") -> None:
        await storage.finish_scan_job(job_id, phase, label)

    async def reap_orphans(self, reason: str = "Прервано рестартом") -> int:
        return await storage.reset_orphaned_scan_jobs(reason)

    @staticmethod
    def _to_api_dict(job: dict) -> dict[str, Any]:
        elapsed = 0.0
        started_at = job.get("started_at")
        finished_at = job.get("finished_at")
        if started_at:
            try:
                start = datetime.fromisoformat(str(started_at))
                if finished_at:
                    end = datetime.fromisoformat(str(finished_at))
                else:
                    end = datetime.utcnow()
                elapsed = round((end - start).total_seconds(), 1)
            except (ValueError, TypeError):
                elapsed = 0.0

        return {
            "running": job.get("status") == "running",
            "phase": job.get("phase", "idle"),
            "phase_label": job.get("phase_label", ""),
            "query": job.get("query", ""),
            "elapsed": elapsed,
            "total": job.get("total", 0),
            "processed": job.get("processed", 0),
            "new_count": job.get("new_count", 0),
            "skipped_count": job.get("skipped_count", 0),
            "current_title": job.get("current_title", ""),
            "current_company": job.get("current_company", ""),
            "error": job.get("error", ""),
            "logs": job.get("logs") or [],
        }
