"""Тесты scan_jobs в БД."""



import os

import tempfile



import pytest



import config

import storage

from repositories import ScanJobRepository





@pytest.fixture

async def scan_db(monkeypatch):

    fd, path = tempfile.mkstemp(suffix=".db")

    os.close(fd)

    monkeypatch.setattr(storage, "DB_PATH", path)
    monkeypatch.setattr(config, "DB_PATH", path)

    await storage.init_db()

    yield path

    try:

        os.unlink(path)

    except OSError:

        pass





@pytest.mark.asyncio

async def test_begin_creates_running_job(scan_db):

    repo = ScanJobRepository()

    job_id = await repo.begin("Python Backend")

    assert job_id is not None

    assert await repo.is_running() is True



    status = await repo.get_status()

    assert status["running"] is True

    assert status["query"] == "Python Backend"

    assert status["phase"] == "queued"

    assert len(status["logs"]) == 1





@pytest.mark.asyncio

async def test_begin_returns_none_when_already_running(scan_db):

    repo = ScanJobRepository()

    first = await repo.begin("Go")

    second = await repo.begin("Rust")

    assert first is not None

    assert second is None





@pytest.mark.asyncio

async def test_finish_marks_job_done(scan_db):

    repo = ScanJobRepository()

    job_id = await repo.begin("Java")

    await repo.finish(job_id, "done", "Скан завершён")



    assert await repo.is_running() is False

    status = await repo.get_status()

    assert status["running"] is False

    assert status["phase"] == "done"

    assert status["phase_label"] == "Скан завершён"





@pytest.mark.asyncio

async def test_logs_bounded(scan_db):

    repo = ScanJobRepository()

    job_id = await repo.begin("X")

    for i in range(100):

        await repo.log(job_id, f"msg {i}")



    status = await repo.get_status()

    assert len(status["logs"]) <= storage.SCAN_LOG_MAX





@pytest.mark.asyncio

async def test_idle_status_when_no_jobs(scan_db):

    repo = ScanJobRepository()

    status = await repo.get_status()

    assert status["running"] is False

    assert status["phase"] == "idle"


