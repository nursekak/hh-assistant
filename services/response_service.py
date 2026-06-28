"""Business logic for employer response checks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

import config
import scraper
from repositories import VacancyRepository

log = logging.getLogger(__name__)

SendMessageFn = Callable[..., Awaitable[Any]]


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ResponseService:
    """Проверка ответов работодателей на HH.ru."""

    def __init__(self, vacancy_repo: VacancyRepository | None = None) -> None:
        self.vacancy_repo = vacancy_repo or VacancyRepository()

    async def check_new_responses(self) -> list[dict]:
        """Возвращает вакансии, по которым пришёл новый ответ работодателя."""
        if not Path(config.SESSION_FILE).exists():
            return []

        try:
            vacancy_ids = await scraper.check_responses()
        except Exception:
            log.exception("Ошибка check_responses")
            return []

        notified: list[dict] = []
        for vid in vacancy_ids:
            existing = await self.vacancy_repo.get(vid)
            if existing and existing.get("status") != "responded":
                await self.vacancy_repo.mark_response_received(vid)
                notified.append(existing)
        return notified

    async def check_and_notify(self, send_message: SendMessageFn, user_id: int) -> int:
        """Проверяет ответы и отправляет уведомления в Telegram. Возвращает их число."""
        responses = await self.check_new_responses()
        for existing in responses:
            vid = existing.get("id", "")
            await send_message(
                user_id,
                f"💬 <b>Ответ от работодателя!</b>\n"
                f"🏢 {_esc(existing.get('company', ''))}\n"
                f"💼 {_esc(existing.get('title', ''))}\n"
                f"🔗 https://hh.ru/vacancy/{vid}",
                parse_mode="HTML",
            )
        return len(responses)
