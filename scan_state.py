"""
Общее состояние текущего сканирования (живое отображение в веб-интерфейсе).
Бот и веб работают в одном процессе, поэтому делят этот синглтон в памяти.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScanState:
    running: bool = False
    phase: str = "idle"          # idle | search | matching | done | error
    phase_label: str = "Ожидание"
    query: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total: int = 0               # всего вакансий к обработке
    processed: int = 0           # обработано
    new_count: int = 0           # отправлено в Telegram
    skipped_count: int = 0       # авто-пропущено по порогу
    current_title: str = ""      # текущая вакансия
    current_company: str = ""
    error: str = ""
    logs: deque = field(default_factory=lambda: deque(maxlen=40))

    def reset(self, query: str) -> None:
        self.running = True
        self.phase = "search"
        self.phase_label = "Поиск вакансий на HH.ru"
        self.query = query
        self.started_at = time.time()
        self.finished_at = None
        self.total = 0
        self.processed = 0
        self.new_count = 0
        self.skipped_count = 0
        self.current_title = ""
        self.current_company = ""
        self.error = ""
        self.logs.clear()
        self.log(f"Скан запущен: «{query}»")

    def log(self, message: str) -> None:
        self.logs.append({"t": time.strftime("%H:%M:%S"), "msg": message})

    def set_phase(self, phase: str, label: str) -> None:
        self.phase = phase
        self.phase_label = label

    def finish(self, phase: str = "done", label: str = "Готово") -> None:
        self.running = False
        self.phase = phase
        self.phase_label = label
        self.finished_at = time.time()

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


STATE = ScanState()
