"""Тесты версионности резюме."""

import os
import tempfile

import pytest

import config
import storage
from repositories import ResumeRepository, ResumeVersionRepository


@pytest.fixture
async def resume_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # storage делает `from config import DB_PATH`, поэтому патчим именно storage.DB_PATH.
    monkeypatch.setattr(storage, "DB_PATH", path)
    monkeypatch.setattr(config, "DB_PATH", path)
    await storage.init_db()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_first_save_creates_version_one(resume_db):
    repo = ResumeRepository()
    versions = ResumeVersionRepository()
    await repo.save("r1", "Backend", raw_text="Python Docker", keywords=["python"])

    items = await versions.list("r1")
    assert len(items) == 1
    assert items[0]["version"] == 1
    assert items[0]["is_current"] is True

    # активная версия зеркалирована в голову
    resumes = await repo.list()
    assert resumes[0]["active_version"] == 1


@pytest.mark.asyncio
async def test_same_text_does_not_create_new_version(resume_db):
    repo = ResumeRepository()
    versions = ResumeVersionRepository()
    await repo.save("r1", "Backend", raw_text="Python Docker", keywords=["python"])
    # повторный парсинг идентичного текста, но новый профиль
    await repo.save("r1", "Backend", raw_text="Python Docker", keywords=["python", "docker"])

    items = await versions.list("r1")
    assert len(items) == 1
    # профиль/ключевые слова текущей версии обновились
    assert "docker" in items[0]["keywords"]


@pytest.mark.asyncio
async def test_changed_text_creates_new_version(resume_db):
    repo = ResumeRepository()
    versions = ResumeVersionRepository()
    await repo.save("r1", "Backend", raw_text="Python Docker")
    await repo.save("r1", "Backend", raw_text="Python Docker FastAPI Redis")

    items = await versions.list("r1")
    assert len(items) == 2
    assert items[0]["version"] == 2
    assert items[0]["is_current"] is True
    assert items[1]["is_current"] is False


@pytest.mark.asyncio
async def test_restore_previous_version(resume_db):
    repo = ResumeRepository()
    versions = ResumeVersionRepository()
    await repo.save("r1", "Backend", raw_text="v1 text", keywords=["a"])
    await repo.save("r1", "Backend", raw_text="v2 text", keywords=["b"])

    ok = await versions.restore("r1", 1)
    assert ok is True

    resumes = await repo.list()
    head = next(r for r in resumes if r["id"] == "r1")
    assert head["active_version"] == 1
    assert head["raw_text"] == "v1 text"

    items = await versions.list("r1")
    current = next(v for v in items if v["is_current"])
    assert current["version"] == 1


@pytest.mark.asyncio
async def test_save_without_text_keeps_snapshot(resume_db):
    repo = ResumeRepository()
    versions = ResumeVersionRepository()
    await repo.save("r1", "Backend", raw_text="real text", keywords=["a"])
    # fetch_from_hh сохраняет только заголовок (raw_text="")
    await repo.save("r1", "Backend Dev")

    items = await versions.list("r1")
    assert len(items) == 1
    resumes = await repo.list()
    head = next(r for r in resumes if r["id"] == "r1")
    assert head["raw_text"] == "real text"
    assert head["title"] == "Backend Dev"
