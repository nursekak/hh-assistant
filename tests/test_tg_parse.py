"""Тесты маппинга Telegram-сообщений → VacancyData (без сети)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tg_parse import (
    TgMessageStub,
    build_post_url,
    channel_id_to_internal,
    extract_title,
    is_within_lookback,
    make_vacancy_id,
    map_message_to_vacancy,
    message_text,
    truncate_text,
)


def test_make_vacancy_id():
    assert make_vacancy_id(-1001234567890, 42) == "tg:-1001234567890:42"


def test_channel_id_to_internal_supergroup():
    assert channel_id_to_internal(-1001234567890) == 1234567890


def test_build_post_url_public():
    assert build_post_url("job_channel", -1001, 99) == "https://t.me/job_channel/99"


def test_build_post_url_private():
    url = build_post_url(None, -1001234567890, 77)
    assert url == "https://t.me/c/1234567890/77"


def test_extract_title_first_line():
    text = "Python Developer\nКомпания ищет…"
    assert extract_title(text) == "Python Developer"


def test_extract_title_truncates():
    long_line = "A" * 120
    title = extract_title(long_line, max_len=80)
    assert len(title) == 80
    assert title.endswith("…")


def test_skip_empty_message():
    msg = TgMessageStub(id=1, date=datetime.now(timezone.utc), text="   ")
    assert map_message_to_vacancy(
        channel_id=-1001,
        channel_title="Jobs",
        username="jobs",
        message=msg,
    ) is None


def test_map_message_basic():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    msg = TgMessageStub(
        id=55,
        date=now - timedelta(hours=2),
        text="Backend Python\nУдалёнка, от 200к",
    )
    vac = map_message_to_vacancy(
        channel_id=-1001234567890,
        channel_title="IT Jobs",
        username="itjobs",
        message=msg,
        lookback_hours=24,
        now=now,
    )
    assert vac is not None
    assert vac.id == "tg:-1001234567890:55"
    assert vac.source == "tg"
    assert vac.title == "Backend Python"
    assert vac.company == "IT Jobs"
    assert vac.url == "https://t.me/itjobs/55"
    assert "Удалёнка" in vac.full_text


def test_lookback_filters_old_messages():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    msg = TgMessageStub(
        id=1,
        date=now - timedelta(hours=48),
        text="Старая вакансия",
    )
    assert map_message_to_vacancy(
        channel_id=-1001,
        channel_title="Jobs",
        username=None,
        message=msg,
        lookback_hours=24,
        now=now,
    ) is None


def test_is_within_lookback_naive_date():
    now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    naive_recent = datetime(2026, 6, 29, 10, 0)  # без tz
    assert is_within_lookback(naive_recent, 24, now) is True


def test_truncate_text():
    assert truncate_text("x" * 9000, max_len=100).endswith("…")
    assert len(truncate_text("x" * 9000, max_len=100)) == 100


def test_message_text_prefers_message_attr():
    msg = TgMessageStub(id=1, date=datetime.now(timezone.utc), text="ignored", message="hello")
    assert message_text(msg) == "hello"


@pytest.mark.parametrize(
    "channel_id,message_id",
    [
        (-100111, 1),
        (-100222333444, 999),
    ],
)
def test_ids_do_not_collide_with_hh_numeric(channel_id: int, message_id: int):
    vid = make_vacancy_id(channel_id, message_id)
    assert vid.startswith("tg:")
    assert ":" in vid
    assert not vid.split(":", 1)[1].isdigit() or vid.count(":") >= 2
