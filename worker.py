"""
ARQ-воркер фоновых задач: сканирование, проверка ответов, перепарсинг резюме.

Запуск (в контейнере):  arq worker.WorkerSettings

Воркер изолирует тяжёлый Playwright/Ollama-пайплайн от процесса бота:
падение/таймаут скана не роняет Telegram, задачи переживают рестарт, есть retry.
Доступ к браузеру сериализуется с ботом через распределённый Redis-лок
(см. distributed_lock.HybridLock внутри scraper.BROWSER_LOCK).
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

from aiogram import Bot
from arq.connections import RedisSettings

import config
import storage
from services import ResponseService, ResumeService, ScanNotifier, ScanService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] worker: %(message)s",
)
log = logging.getLogger("worker")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def scan_task(ctx: dict) -> None:
    bot: Bot = ctx["bot"]
    notifier = ScanNotifier(send_message=bot.send_message, user_id=config.ALLOWED_USER_ID)
    service = ScanService(notifier=notifier)
    await service.run(attempt=ctx.get("job_try", 1), worker_id=WORKER_ID)


async def check_responses_task(ctx: dict) -> None:
    bot: Bot = ctx["bot"]
    count = await ResponseService().check_and_notify(bot.send_message, config.ALLOWED_USER_ID)
    if count:
        log.info("Новых ответов работодателей: %d", count)


async def reparse_resume_task(ctx: dict) -> None:
    service = ResumeService()
    active = await service.resume_repo.get_active()
    if not active or not Path(config.SESSION_FILE).exists():
        return
    try:
        await service.reparse(active["id"])
        log.info("Ключевые слова резюме обновлены")
    except Exception:
        log.exception("Ошибка перепарсинга резюме")


async def startup(ctx: dict) -> None:
    await storage.init_db()
    reaped = await storage.reset_orphaned_scan_jobs()
    if reaped:
        log.info("Сброшено зависших задач: %d", reaped)
    ctx["bot"] = Bot(token=config.TELEGRAM_TOKEN)
    log.info("Воркер %s запущен", WORKER_ID)


async def shutdown(ctx: dict) -> None:
    bot = ctx.get("bot")
    if bot is not None:
        await bot.session.close()
    log.info("Воркер %s остановлен", WORKER_ID)


class WorkerSettings:
    functions = [scan_task, check_responses_task, reparse_resume_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL or "redis://localhost:6379")
    max_tries = config.SCAN_JOB_MAX_TRIES
    job_timeout = config.SCAN_JOB_TIMEOUT_SEC
    # Параллельно держим не больше 2 задач: scan и responses/reparse могут идти
    # одновременно, но доступ к браузеру всё равно сериализуется Redis-локом.
    max_jobs = 2
