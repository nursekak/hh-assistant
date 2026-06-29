"""Business logic for application settings."""

from __future__ import annotations

from dataclasses import dataclass

import config
import tg_client
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
    hh_employment: str
    candidate_experience_years: str
    salary_from: int
    only_with_salary: bool
    # Matching
    threshold_pct: int
    notify_below_threshold: bool
    experience_filter: bool
    experience_tolerance: float
    # AI
    ollama_model: str
    cover_letter_backend: str
    anthropic_api_key: str
    anthropic_model: str
    candidate_name: str
    # Scheduler
    interval: int
    # Telegram channels
    tg_scan_enabled: bool
    tg_channels_folder: str
    tg_lookback_hours: int
    tg_max_messages_per_channel: int
    tg_session_ok: bool
    tg_credentials_ok: bool


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
        hh_employment = await self.settings_repo.get("hh_employment", "")
        candidate_experience_years = await self.settings_repo.get(
            "candidate_experience_years", ""
        )
        salary_from = await self._get_int("salary_from", 0)
        only_with_salary = await self._get_bool("only_with_salary", False)

        threshold = await self.settings_repo.get_match_threshold(config.MIN_MATCH_THRESHOLD)
        notify_below_threshold = await self._get_bool(
            "notify_below_threshold",
            config.NOTIFY_BELOW_THRESHOLD,
        )
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
        candidate_name = await self.settings_repo.get("candidate_name", config.CANDIDATE_NAME)

        interval = await self._get_int("scan_interval_hours", config.SCAN_INTERVAL_HOURS)

        tg_scan_enabled = await self._get_bool("tg_scan_enabled", config.TG_SCAN_ENABLED)
        tg_channels_folder = await self.settings_repo.get(
            "tg_channels_folder", config.TG_CHANNELS_FOLDER
        )
        tg_lookback_hours = await self._get_int("tg_lookback_hours", config.TG_LOOKBACK_HOURS)
        tg_max_messages_per_channel = await self._get_int(
            "tg_max_messages_per_channel", config.TG_MAX_MESSAGES_PER_CHANNEL
        )

        return SettingsView(
            query=query,
            region=region,
            period=period,
            max_vacancies=max_vacancies,
            hh_schedule=hh_schedule,
            hh_employment=hh_employment,
            candidate_experience_years=candidate_experience_years,
            salary_from=salary_from,
            only_with_salary=only_with_salary,
            threshold_pct=int(threshold * 100),
            notify_below_threshold=notify_below_threshold,
            experience_filter=experience_filter,
            experience_tolerance=experience_tolerance,
            ollama_model=ollama_model,
            cover_letter_backend=cover_letter_backend,
            anthropic_api_key=anthropic_api_key,
            anthropic_model=anthropic_model,
            candidate_name=candidate_name,
            interval=interval,
            tg_scan_enabled=tg_scan_enabled,
            tg_channels_folder=tg_channels_folder,
            tg_lookback_hours=tg_lookback_hours,
            tg_max_messages_per_channel=tg_max_messages_per_channel,
            tg_session_ok=tg_client.has_session(),
            tg_credentials_ok=tg_client.has_credentials(),
        )

    async def save(self, form: dict[str, object]) -> int:
        """Сохраняет настройки и возвращает примененный интервал сканирования."""
        interval = max(1, int(form.get("interval", 2)))
        max_vacancies = max(1, min(int(form.get("max_vacancies", 50)), 200))
        salary_from = max(0, int(form.get("salary_from", 0)))
        threshold_pct = int(form.get("threshold_pct", 50))
        cover_letter_backend = str(form.get("cover_letter_backend", "ollama"))

        values = {
            "query": str(form.get("query", "")).strip(),
            "hh_region": str(form.get("region", "1")),
            "hh_search_period": str(int(form.get("period", 1))),
            "max_vacancies": str(max_vacancies),
            "hh_schedule": str(form.get("hh_schedule", "")),
            "hh_employment": str(form.get("hh_employment", "")),
            "candidate_experience_years": self._clean_experience_years(
                form.get("candidate_experience_years", "")
            ),
            "salary_from": str(salary_from),
            "only_with_salary": "true" if form.get("only_with_salary") else "false",
            "min_match_threshold": str(threshold_pct / 100),
            "notify_below_threshold": "on" if form.get("notify_below_threshold") else "off",
            "experience_filter": "on" if form.get("experience_filter") else "off",
            "scan_interval_hours": str(interval),
            "tg_scan_enabled": "on" if form.get("tg_scan_enabled") else "off",
            "tg_channels_folder": str(form.get("tg_channels_folder", "")).strip(),
            "tg_lookback_hours": str(
                max(1, min(int(form.get("tg_lookback_hours", config.TG_LOOKBACK_HOURS)), 168))
            ),
            "tg_max_messages_per_channel": str(
                max(1, min(int(form.get("tg_max_messages_per_channel", 30)), 200))
            ),
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

        # Имя кандидата сохраняем всегда (в т.ч. пустую строку — намеренная очистка).
        values["candidate_name"] = str(form.get("candidate_name", "")).strip()

        await self.settings_repo.set_many(values)
        return interval

    async def set_match_threshold(self, pct: int) -> None:
        await self.settings_repo.set("min_match_threshold", str(pct / 100))

    async def set_query(self, query: str) -> None:
        await self.settings_repo.set("query", query.strip())

    async def reset_statistics(self) -> int:
        return await self.vacancy_repo.reset_statistics()

    async def list_tg_folders(self) -> list[dict[str, object]]:
        try:
            rows = await tg_client.list_folders()
            return [{"title": title, "channels_count": count} for title, count in rows]
        except Exception as exc:
            return [{"error": str(exc)}]

    @staticmethod
    def _clean_experience_years(value: object) -> str:
        """Нормализует «мой опыт, лет»: пусто/мусор → '', иначе число >= 0."""
        raw = str(value or "").strip().replace(",", ".")
        if not raw:
            return ""
        try:
            years = max(0.0, float(raw))
        except ValueError:
            return ""
        # Целые показываем без .0 (5, а не 5.0).
        return str(int(years)) if years.is_integer() else str(round(years, 1))

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
