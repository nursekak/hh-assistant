"""
Состояние сканирования хранится в таблице scan_jobs (см. storage.py, ScanJobRepository).
Этот модуль оставлен для обратной совместимости тестов и документации API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScanState:
    """In-memory модель ответа /api/scan/status (не синглтон)."""

    running: bool = False
    phase: str = "idle"
    phase_label: str = "Ожидание"
    query: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total: int = 0
    processed: int = 0
    new_count: int = 0
    skipped_count: int = 0
    current_title: str = ""
    current_company: str = ""
    error: str = ""
    logs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        elapsed = 0.0
        if self.started_at:
            end = self.finished_at or time.time()
            elapsed = round(end - self.started_at, 1)
        return {
            "running": self.running,
            "phase": self.phase,
            "phase_label": self.phase_label,
            "query": self.query,
            "elapsed": elapsed,
            "total": self.total,
            "processed": self.processed,
            "new_count": self.new_count,
            "skipped_count": self.skipped_count,
            "current_title": self.current_title,
            "current_company": self.current_company,
            "error": self.error,
            "logs": list(self.logs),
        }
