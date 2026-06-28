"""Settings repository."""

from __future__ import annotations

import storage


class SettingsRepository:
    """Доступ к key-value настройкам приложения."""

    async def get(self, key: str, default: str = "") -> str:
        return await storage.get_setting(key, default)

    async def set(self, key: str, value: str) -> None:
        await storage.set_setting(key, value)

    async def get_match_threshold(self, default: float) -> float:
        return await storage.get_min_match_threshold(default)

    async def set_many(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            await self.set(key, value)
