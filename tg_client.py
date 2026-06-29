"""Telethon user-client: папки Telegram и сообщения каналов → VacancyData."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

import config
from distributed_lock import HybridLock
from vacancy_types import VacancyData
from tg_parse import map_message_to_vacancy

log = logging.getLogger(__name__)

TG_USER_LOCK = HybridLock("tg:user")

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl.functions.messages import GetDialogFiltersRequest
    from telethon.tl.types import Channel, DialogFilter
except ImportError:  # pragma: no cover
    TelegramClient = None  # type: ignore[assignment,misc]
    SessionPasswordNeededError = Exception  # type: ignore[assignment,misc]
    GetDialogFiltersRequest = None  # type: ignore[assignment,misc]
    Channel = None  # type: ignore[assignment,misc]
    DialogFilter = None  # type: ignore[assignment,misc]


def session_file_path() -> Path:
    base = Path(config.TG_USER_SESSION)
    if base.suffix == ".session":
        return base
    return Path(f"{config.TG_USER_SESSION}.session")


def has_credentials() -> bool:
    return bool(config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH)


def has_session() -> bool:
    return session_file_path().is_file()


def is_configured() -> bool:
    return has_credentials() and has_session()


def _filter_title(dialog_filter: object) -> str:
    title = getattr(dialog_filter, "title", "")
    if hasattr(title, "text"):
        return str(title.text or "").strip()
    return str(title or "").strip()


def get_client() -> "TelegramClient":
    if TelegramClient is None:
        raise RuntimeError("telethon не установлен — добавьте пакет в requirements.txt")
    if not has_credentials():
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы")
    return TelegramClient(
        config.TG_USER_SESSION,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )


# ── Интерактивный логин из бота (телефон → код → 2FA) ──────────────────────
#
# Telethon-клиент держит .session открытым между шагами, поэтому объект клиента
# хранится у вызывающего (bot.py) и сериализуется через TG_USER_LOCK на всё
# время логина (чтобы фоновый скан не трогал ту же сессию).


async def begin_login() -> "TelegramClient":
    """Создаёт и подключает клиента для интерактивного входа."""
    client = get_client()
    await client.connect()
    return client


async def send_login_code(client: "TelegramClient", phone: str) -> str:
    """Запрашивает код подтверждения. Возвращает phone_code_hash."""
    sent = await client.send_code_request(phone)
    return sent.phone_code_hash


async def complete_login_code(
    client: "TelegramClient",
    phone: str,
    code: str,
    phone_code_hash: str,
) -> bool:
    """
    Подтверждает вход кодом. Возвращает True, если вход завершён.
    Бросает SessionPasswordNeededError, если включена 2FA (нужен пароль).
    """
    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    return await client.is_user_authorized()


async def complete_login_password(client: "TelegramClient", password: str) -> bool:
    """Завершает вход паролем 2FA. Возвращает True при успехе."""
    await client.sign_in(password=password)
    return await client.is_user_authorized()


async def list_folders() -> list[tuple[str, int]]:
    """Возвращает [(название_папки, число_пиров_в_папке), ...]."""
    async with TG_USER_LOCK:
        client = get_client()
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram user-session не авторизована — запустите tg_login.py")
            result = await client(GetDialogFiltersRequest())
            folders: list[tuple[str, int]] = []
            for item in result.filters:
                if not isinstance(item, DialogFilter):
                    continue
                peers = list(item.include_peers or []) + list(item.pinned_peers or [])
                folders.append((_filter_title(item), len(peers)))
            return folders
        finally:
            await client.disconnect()


async def fetch_folder_channels(client: "TelegramClient", folder_name: str) -> list[Channel]:
    """Каналы (broadcast/megagroup) из папки по имени."""
    if not folder_name.strip():
        return []

    result = await client(GetDialogFiltersRequest())
    target = None
    wanted = folder_name.strip().casefold()
    for item in result.filters:
        if not isinstance(item, DialogFilter):
            continue
        if _filter_title(item).casefold() == wanted:
            target = item
            break

    if target is None:
        log.warning("Папка Telegram «%s» не найдена", folder_name)
        return []

    peers = list(target.include_peers or []) + list(target.pinned_peers or [])
    channels: list[Channel] = []
    for peer in peers:
        try:
            entity = await client.get_entity(peer)
        except Exception as exc:
            log.debug("Не удалось разрешить peer %s: %s", peer, exc)
            continue
        if isinstance(entity, Channel) and (entity.broadcast or entity.megagroup):
            channels.append(entity)
    return channels


async def fetch_new_messages(
    folder_name: str,
    lookback_hours: int,
    max_per_channel: int,
    is_seen: Callable[[str], Awaitable[bool]] | None = None,
) -> list[VacancyData]:
    """
    Читает новые сообщения из каналов указанной папки.
    Дедуп: is_seen(vacancy_id) + lookback по дате сообщения.
    """
    if not is_configured():
        raise RuntimeError("Telegram user-session не настроена (API_ID/HASH или файл сессии)")

    async with TG_USER_LOCK:
        client = get_client()
        await client.connect()
        vacancies: list[VacancyData] = []
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram user-session не авторизована — запустите tg_login.py")

            channels = await fetch_folder_channels(client, folder_name)
            log.info(
                "TG: папка «%s» — %d канал(ов), lookback=%dh, лимит=%d/канал",
                folder_name,
                len(channels),
                lookback_hours,
                max_per_channel,
            )

            for channel in channels:
                channel_title = channel.title or "Telegram-канал"
                username = getattr(channel, "username", None)
                fetched = 0

                async for message in client.iter_messages(channel, limit=max_per_channel):
                    vac = map_message_to_vacancy(
                        channel_id=channel.id,
                        channel_title=channel_title,
                        username=username,
                        message=message,
                        lookback_hours=lookback_hours,
                    )
                    if vac is None:
                        continue
                    if is_seen and await is_seen(vac.id):
                        continue
                    vacancies.append(vac)
                    fetched += 1

                log.debug("TG: канал «%s» — %d сообщений в выборке", channel_title, fetched)

        finally:
            await client.disconnect()

    return vacancies
