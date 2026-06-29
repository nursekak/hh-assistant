"""
Гибридный лок для сериализации доступа к браузеру (Playwright).

Внутри одного процесса работает как asyncio.Lock. Если задан REDIS_URL —
дополнительно берётся распределённый Redis-лок, чтобы бот (login/apply) и
воркер (scan/responses/reparse) не управляли одним браузером одновременно.

Если Redis недоступен, прозрачно деградирует до локального asyncio.Lock, поэтому
локальный запуск и тесты работают без Redis.
"""

from __future__ import annotations

import asyncio
import logging

import config

try:  # redis опционален: без него остаётся только локальный лок
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


class HybridLock:
    """asyncio.Lock + (опционально) распределённый Redis-лок."""

    def __init__(self, name: str = "hh:browser", ttl_sec: int | None = None) -> None:
        self._local = asyncio.Lock()
        self._name = name
        self._ttl = ttl_sec or config.BROWSER_LOCK_TTL_SEC
        self._client = None
        self._active_rlock = None

    async def __aenter__(self) -> "HybridLock":
        await self._local.acquire()
        if aioredis is not None and config.REDIS_URL:
            try:
                if self._client is None:
                    self._client = aioredis.from_url(config.REDIS_URL)
                rlock = self._client.lock(
                    self._name,
                    timeout=self._ttl,
                    blocking_timeout=self._ttl,
                )
                acquired = await rlock.acquire()
                self._active_rlock = rlock if acquired else None
            except Exception as e:  # сеть/Redis недоступен — деградируем
                log.warning("Redis-лок недоступен, работаю локально: %s", e)
                self._active_rlock = None
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._active_rlock is not None:
            try:
                await self._active_rlock.release()
            except Exception:  # лок мог истечь по TTL
                pass
            self._active_rlock = None
        self._local.release()

    async def acquire(self) -> "HybridLock":
        """Ручной захват (для многошаговых сценариев, напр. интерактивный логин)."""
        return await self.__aenter__()

    async def release(self) -> None:
        """Ручное освобождение, парное к acquire()."""
        await self.__aexit__()

    def locked(self) -> bool:
        return self._local.locked()
