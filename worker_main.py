"""
Точка входа воркера: python worker_main.py

Запускаем ARQ через run_worker, а не через CLI `arq`, потому что CLI вызывает
uvloop.install() (uvloop тянется uvicorn[standard]), а связка
arq 0.26 + uvloop + Python 3.10 падает на asyncio.get_event_loop() при инициализации.
Предварительно гарантируем наличие event loop в главном потоке.
"""

import asyncio

from arq import run_worker

from worker import WorkerSettings

if __name__ == "__main__":
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    run_worker(WorkerSettings)
