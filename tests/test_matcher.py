"""Тесты матчинга вакансия ↔ резюме."""

import numpy as np
import pytest

import embeddings
import matcher
from extractor import ResumeProfile, VacancyProfile


@pytest.fixture(autouse=True)
def _no_network_embeddings(monkeypatch):
    """Принудительно роняем эмбеддинги → matcher уходит в exact-фолбэк."""
    async def _raise(_texts):
        raise RuntimeError("offline")
    monkeypatch.setattr(embeddings, "embed", _raise)


def test_exact_match_sets():
    matched, missing, extra = matcher._exact_match_sets(
        ["python", "docker"], ["python", "redis"]
    )
    assert matched == {"python"}
    assert missing == {"docker"}
    assert extra == {"redis"}


async def test_perfect_match_passes():
    vac = VacancyProfile(hard_skills=["Python", "Docker"])
    res = ResumeProfile(skills=["python"], stack=["docker"])
    result = await matcher.compute_match(vac, res, threshold=0.65)
    assert result.score_pct == 100
    assert result.verdict == "PASS"
    assert set(result.matched) == {"python", "docker"}
    assert result.missing == []


async def test_partial_match_below_threshold():
    vac = VacancyProfile(hard_skills=["Python", "Docker"])
    res = ResumeProfile(skills=["python"], stack=["redis"])
    result = await matcher.compute_match(vac, res, threshold=0.65)
    assert result.verdict == "SKIP"
    assert "docker" in result.missing
    assert "redis" in result.extra
    assert "python" in result.matched


async def test_tfidf_fallback_when_no_skills():
    vac = VacancyProfile()
    res = ResumeProfile()
    result = await matcher.compute_match(
        vac, res,
        vacancy_text="Python developer with Django and PostgreSQL",
        resume_text="Python developer, Django, PostgreSQL, Redis",
        threshold=0.65,
    )
    assert result.score > 0.0


def test_cosine_matrix_identity():
    a = np.array([[1.0, 0.0], [0.0, 1.0]])
    sim = embeddings.cosine_matrix(a, a)
    assert sim[0, 0] == pytest.approx(1.0, abs=1e-6)
    assert sim[0, 1] == pytest.approx(0.0, abs=1e-6)


def test_format_match_line_contains_percent():
    result = matcher.MatchResult(
        score=0.5, score_pct=50, matched=["python"], missing=["docker"],
        extra=[], verdict="SKIP",
    )
    line = matcher.format_match_line(result)
    assert "50%" in line
    assert "python" in line
