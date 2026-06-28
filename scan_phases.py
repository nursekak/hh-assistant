"""
Явная state machine фаз сканирования (Job Manager).

Фаза хранится в scan_jobs.phase. Прогресс в % веб-интерфейс считает по
processed/total, но порядок фаз даёт грубую оценку до начала обработки.
"""

from __future__ import annotations

QUEUED = "queued"
SEARCHING = "searching"
MATCHING = "matching"
FINALIZING = "finalizing"
DONE = "done"
ERROR = "error"
IDLE = "idle"

# Терминальные фазы — задача завершена.
TERMINAL = frozenset({DONE, ERROR})

# Порядок «живых» фаз для грубого прогресса.
ORDER = [QUEUED, SEARCHING, MATCHING, FINALIZING, DONE]

LABELS = {
    QUEUED: "В очереди",
    SEARCHING: "Поиск вакансий на HH.ru",
    MATCHING: "Анализ вакансий",
    FINALIZING: "Завершение",
    DONE: "Скан завершён",
    ERROR: "Ошибка",
    IDLE: "Ожидание",
}


def label(phase: str) -> str:
    return LABELS.get(phase, phase)
