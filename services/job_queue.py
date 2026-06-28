"""Очередь фоновых задач поверх ARQ (Redis).

Если REDIS_URL не задан или Redis недоступен, задача выполняется inline
(asyncio.create_task с переданным fallback), поэтому система работает и без
отдельного воркера — удобно для локального запуска и тестов.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import config

try:
    from arq import create_pool
    from arq.connections import RedisSettings
except ImportError:  # pragma: no cover
    create_pool = None  # type: ignore[assignment]
    RedisSettings = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

InlineFn = Callable[[], Awaitable[None]]


class JobQueue:
    """Тонкая обёртка над ARQ-пулом с graceful fallback на inline-выполнение."""

    def __init__(self, redis_url: str = "") -> None:
        self.redis_url = redis_url or config.REDIS_URL
        self._pool = None
        self._pool_failed = False

    @property
    def enabled(self) -> bool:
        return bool(self.redis_url) and create_pool is not None

    async def _get_pool(self):
        if not self.enabled or self._pool_failed:
            return None
        if self._pool is None:
            try:
                self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
            except Exception as e:
                log.warning("Не удалось подключиться к Redis (%s) — fallback inline", e)
                self._pool_failed = True
                return None
        return self._pool

    async def enqueue(
        self,
        task_name: str,
        *args,
        fallback: Optional[InlineFn] = None,
    ) -> bool:
        """Ставит задачу в очередь. Возвращает True, если ушла в Redis.

        При недоступности Redis запускает fallback в текущем процессе.
        """
        pool = await self._get_pool()
        if pool is not None:
            try:
                await pool.enqueue_job(task_name, *args)
                log.info("Задача %s поставлена в очередь", task_name)
                return True
            except Exception as e:
                log.warning("Ошибка enqueue %s (%s) — fallback inline", task_name, e)

        if fallback is not None:
            asyncio.create_task(fallback())
        return False

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
