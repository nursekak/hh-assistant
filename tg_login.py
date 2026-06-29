#!/usr/bin/env python3
"""Одноразовый интерактивный логин Telethon user-session.

Запуск:
  python tg_login.py
  docker compose run --rm worker python tg_login.py

Создаёт файл сессии (по умолчанию data/tg_user.session).
"""

from __future__ import annotations

import asyncio
import sys

import config


async def main() -> None:
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        print(
            "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env "
            "(https://my.telegram.org → API development tools)",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from telethon import TelegramClient
    except ImportError:
        print("Установите telethon: pip install telethon", file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(
        config.TG_USER_SESSION,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await client.start()
    me = await client.get_me()
    name = getattr(me, "first_name", "") or getattr(me, "username", "user")
    print(f"Сессия сохранена для {name} → {config.TG_USER_SESSION}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
