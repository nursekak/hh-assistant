"""Чистые функции маппинга Telegram-сообщений → VacancyData (без сети)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from vacancy_types import VacancyData

MAX_FULL_TEXT_LEN = 8000
TITLE_MAX_LEN = 80


def make_vacancy_id(channel_id: int, message_id: int) -> str:
    return f"tg:{channel_id}:{message_id}"


def channel_id_to_internal(channel_id: int) -> int:
    """Внутренний id для ссылки t.me/c/<id>/<msg> (без префикса -100)."""
    raw = abs(int(channel_id))
    s = str(raw)
    if s.startswith("100") and len(s) > 3:
        return int(s[3:])
    return raw


def build_post_url(
    username: str | None,
    channel_id: int,
    message_id: int,
) -> str:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    internal = channel_id_to_internal(channel_id)
    return f"https://t.me/c/{internal}/{message_id}"


def extract_title(text: str, max_len: int = TITLE_MAX_LEN) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    first_line = cleaned.splitlines()[0].strip()
    title = first_line or cleaned
    if len(title) > max_len:
        return title[: max_len - 1].rstrip() + "…"
    return title


def extract_salary(text: str) -> str:
    """Best-effort извлечение зарплаты из текста поста."""
    if not text:
        return ""
    patterns = [
        r"(?:от\s+)?(\d[\d\s]{2,})\s*(?:₽|руб\.?|RUB)",
        r"(\d[\d\s]{2,})\s*[-–]\s*(\d[\d\s]{2,})\s*(?:₽|руб\.?|RUB)",
        r"\$\s*(\d[\d\s,]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def truncate_text(text: str, max_len: int = MAX_FULL_TEXT_LEN) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


@dataclass(slots=True)
class TgMessageStub:
    """Минимальный объект сообщения для тестов и маппинга."""

    id: int
    date: datetime
    text: str = ""
    message: str = ""


def message_text(msg: TgMessageStub | object) -> str:
    raw = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
    return str(raw).strip()


def is_within_lookback(msg_date: datetime, lookback_hours: int, now: datetime | None = None) -> bool:
    if lookback_hours <= 0:
        return True
    ref = now or datetime.now(timezone.utc)
    if msg_date.tzinfo is None:
        msg_date = msg_date.replace(tzinfo=timezone.utc)
    since = ref - timedelta(hours=lookback_hours)
    return msg_date >= since


def map_message_to_vacancy(
    *,
    channel_id: int,
    channel_title: str,
    username: str | None,
    message: TgMessageStub | object,
    lookback_hours: int = 24,
    now: datetime | None = None,
) -> VacancyData | None:
    """Преобразует одно сообщение канала в VacancyData или None (пропуск)."""
    text = message_text(message)
    if not text:
        return None

    msg_date = getattr(message, "date", None)
    if msg_date is not None and not is_within_lookback(msg_date, lookback_hours, now):
        return None

    message_id = int(getattr(message, "id"))
    full_text = truncate_text(text)
    title = extract_title(full_text) or channel_title or "Вакансия из Telegram"

    return VacancyData(
        id=make_vacancy_id(channel_id, message_id),
        title=title,
        company=channel_title or "Telegram-канал",
        salary=extract_salary(full_text),
        url=build_post_url(username, channel_id, message_id),
        full_text=full_text,
        experience="",
        source="tg",
    )
