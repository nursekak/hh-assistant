"""Repository layer.

Репозитории инкапсулируют доступ к текущему storage.py. Пока это тонкие
адаптеры над aiosqlite-функциями, но внешний код уже не обязан знать про
конкретную реализацию хранения.
"""

from repositories.analytics import AnalyticsRepository
from repositories.resume import ResumeRepository
from repositories.scan_job import ScanJobRepository
from repositories.settings import SettingsRepository
from repositories.vacancy import VacancyRepository

__all__ = [
    "AnalyticsRepository",
    "ResumeRepository",
    "ScanJobRepository",
    "SettingsRepository",
    "VacancyRepository",
]
