"""Business logic for application settings."""

from __future__ import annotations

from dataclasses import dataclass

import config
from repositories import SettingsRepository, VacancyRepository


TRUE_VALUES = ("1", "true", "yes", "on")


@dataclass(slots=True)
class SettingsView:
    # Search
    query: str
    region: str
    period: int
    max_vacancies: int
    hh_schedule: str
    salary_from: int
    only_with_salary: bool
    # Matching
    threshold_pct: int
    experience_filter: bool
    experience_tolerance: float
    # AI
    ollama_model: str
    cover_letter_backend: str
    anthropic_api_key: str
    anthropic_model: str
    # Scheduler
    interval: int


class SettingsService:
    """Настройки приложения как бизнес-объект, а не набор raw key-value вызовов."""

    def __init__(
        self,
        settings_repo: SettingsRepository | None = None,
        vacancy_repo: VacancyRepository | None = None,
    ) -> None:
        self.settings_repo = settings_repo or SettingsRepository()
        self.vacancy_repo = vacancy_repo or VacancyRepository()

    async def get_view(self) -> SettingsView:
        query = await self.settings_repo.get("query", config.DEFAULT_QUERY)
        region = await self.settings_repo.get("hh_region", config.HH_REGION)
        period = await self._get_int("hh_search_period", config.HH_SEARCH_PERIOD)
        max_vacancies = await self._get_int("max_vacancies", config.MAX_VACANCIES)
        hh_schedule = await self.settings_repo.get("hh_schedule", "")
        salary_from = await self._get_int("salary_from", 0)
        only_with_salary = await self._get_bool("only_with_salary", False)

        threshold = await self.settings_repo.get_match_threshold(config.MIN_MATCH_THRESHOLD)
        experience_filter = await self._get_bool(
            "experience_filter",
            config.EXPERIENCE_FILTER_ENABLED,
        )
        experience_tolerance = await self._get_float(
            "experience_tolerance_years",
            config.EXPERIENCE_TOLERANCE_YEARS,
        )

        ollama_model = await self.settings_repo.get("ollama_model", config.OLLAMA_MODEL)
        cover_letter_backend = await self.settings_repo.get(
            "cover_letter_backend",
            config.COVER_LETTER_BACKEND,
        )
        anthropic_api_key = await self.settings_repo.get("anthropic_api_key", "")
        anthropic_model = await self.settings_repo.get(
            "anthropic_model",
            config.ANTHROPIC_MODEL,
        )

        interval = await self._get_int("scan_interval_hours", config.SCAN_INTERVAL_HOURS)

        return SettingsView(
            query=query,
            region=region,
            period=period,
            max_vacancies=max_vacancies,
            hh_schedule=hh_schedule,
            salary_from=salary_from,
            only_with_salary=only_with_salary,
            threshold_pct=int(threshold * 100),
            experience_filter=experience_filter,
            experience_tolerance=experience_tolerance,
            ollama_model=ollama_model,
            cover_letter_backend=cover_letter_backend,
            anthropic_api_key=anthropic_api_key,
            anthropic_model=anthropic_model,
            interval=interval,
        )

    async def save(self, form: dict[str, object]) -> int:
        """Сохраняет настройки и возвращает примененный интервал сканирования."""
        interval = max(1, int(form.get("interval", 2)))
        max_vacancies = max(1, int(form.get("max_vacancies", 15)))
        salary_from = max(0, int(form.get("salary_from", 0)))
        threshold_pct = int(form.get("threshold_pct", 50))
        cover_letter_backend = str(form.get("cover_letter_backend", "ollama"))

        values = {
            "query": str(form.get("query", "")).strip(),
            "hh_region": str(form.get("region", "1")),
            "hh_search_period": str(int(form.get("period", 1))),
            "max_vacancies": str(max_vacancies),
            "hh_schedule": str(form.get("hh_schedule", "")),
            "salary_from": str(salary_from),
            "only_with_salary": "true" if form.get("only_with_salary") else "false",
            "min_match_threshold": str(threshold_pct / 100),
            "experience_filter": "on" if form.get("experience_filter") else "off",
            "scan_interval_hours": str(interval),
        }

        try:
            values["experience_tolerance_years"] = str(
                round(float(form.get("experience_tolerance", 0.5)), 2)
            )
        except (TypeError, ValueError):
            pass

        ollama_model = str(form.get("ollama_model", "")).strip()
        if ollama_model:
            values["ollama_model"] = ollama_model

        if cover_letter_backend in ("ollama", "claude"):
            values["cover_letter_backend"] = cover_letter_backend

        anthropic_api_key = str(form.get("anthropic_api_key", "")).strip()
        if anthropic_api_key:
            values["anthropic_api_key"] = anthropic_api_key

        anthropic_model = str(form.get("anthropic_model", "")).strip()
        if anthropic_model:
            values["anthropic_model"] = anthropic_model

        await self.settings_repo.set_many(values)
        return interval

    async def set_match_threshold(self, pct: int) -> None:
        await self.settings_repo.set("min_match_threshold", str(pct / 100))

    async def set_query(self, query: str) -> None:
        await self.settings_repo.set("query", query.strip())

    async def reset_statistics(self) -> int:
        return await self.vacancy_repo.reset_statistics()

    async def _get_bool(self, key: str, default: bool) -> bool:
        raw = await self.settings_repo.get(key, "")
        return raw.lower() in TRUE_VALUES if raw else default

    async def _get_int(self, key: str, default: int) -> int:
        try:
            return int(await self.settings_repo.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    async def _get_float(self, key: str, default: float) -> float:
        try:
            return float(await self.settings_repo.get(key, str(default)))
        except (TypeError, ValueError):
            return default
