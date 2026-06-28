"""Тесты построения URL поиска HH с фильтрами (hh_filters)."""

from urllib.parse import parse_qs, urlsplit

import pytest

import hh_filters as hf


# ─────────────────────────── experience_codes ──────────────────────────────

@pytest.mark.parametrize(
    "years, expected",
    [
        (None, []),
        ("", []),
        (0, ["noExperience"]),
        (0.5, ["noExperience"]),
        (1, ["noExperience", "between1And3"]),
        (2, ["noExperience", "between1And3"]),          # пример из ТЗ: 2 → 1-3 года
        (3, ["noExperience", "between1And3", "between3And6"]),
        (5, ["noExperience", "between1And3", "between3And6"]),
        (6, ["noExperience", "between1And3", "between3And6", "moreThan6"]),
        (10, ["noExperience", "between1And3", "between3And6", "moreThan6"]),
        (-1, []),
        ("abc", []),
    ],
)
def test_experience_codes(years, expected):
    assert hf.experience_codes(years) == expected


def test_experience_years_accepts_string_number():
    assert hf.experience_codes("2") == ["noExperience", "between1And3"]


# ─────────────────────────── work_format_codes ─────────────────────────────

def test_work_format_single():
    assert hf.work_format_codes("remote") == ["REMOTE"]


def test_work_format_multiple_and_dedup():
    assert hf.work_format_codes("remote, hybrid, remote") == ["REMOTE", "HYBRID"]


def test_work_format_russian_and_unknown():
    assert hf.work_format_codes("удалёнка") == ["REMOTE"]
    assert hf.work_format_codes("чтоугодно") == []
    assert hf.work_format_codes("") == []


# ─────────────────────────── employment_codes ──────────────────────────────

def test_employment_codes_filters_invalid():
    assert hf.employment_codes("full") == ["full"]
    assert hf.employment_codes("full, part") == ["full", "part"]
    assert hf.employment_codes("bogus") == []
    assert hf.employment_codes("") == []


# ─────────────────────────── classify_schedule ─────────────────────────────

@pytest.mark.parametrize(
    "value, expected",
    [
        ("", ("", [])),
        ("remote", ("", ["REMOTE"])),       # удалёнка → work_format
        ("hybrid", ("", ["HYBRID"])),       # гибрид → work_format
        ("fullDay", ("fullDay", [])),       # настоящий график
        ("flexible", ("flexible", [])),
        ("unknown", ("", [])),
    ],
)
def test_classify_schedule(value, expected):
    assert hf.classify_schedule(value) == expected


# ─────────────────────────── build_search_url ──────────────────────────────

def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_build_url_base_params():
    url = hf.build_search_url("Python Backend", region=2, period=7, per_page=50)
    assert url.startswith("https://hh.ru/search/vacancy?")
    qs = _qs(url)
    assert qs["text"] == ["Python Backend"]
    assert qs["area"] == ["2"]
    assert qs["order_by"] == ["publication_time"]
    assert qs["search_period"] == ["7"]
    assert qs["per_page"] == ["50"]


def test_build_url_experience_multi():
    url = hf.build_search_url("dev", experience_years=2)
    qs = _qs(url)
    assert qs["experience"] == ["noExperience", "between1And3"]


def test_build_url_no_experience_when_none():
    url = hf.build_search_url("dev", experience_years=None)
    assert "experience=" not in url


def test_build_url_remote_goes_to_work_format():
    url = hf.build_search_url("dev", schedule="remote")
    qs = _qs(url)
    assert qs["work_format"] == ["REMOTE"]
    assert "schedule" not in qs            # remote не уходит в schedule


def test_build_url_real_schedule_kept():
    url = hf.build_search_url("dev", schedule="fullDay")
    qs = _qs(url)
    assert qs["schedule"] == ["fullDay"]
    assert "work_format" not in qs


def test_build_url_employment():
    url = hf.build_search_url("dev", employment="full")
    assert _qs(url)["employment"] == ["full"]


def test_build_url_work_format_dedup_with_schedule():
    # schedule=remote (→REMOTE) + work_format=remote не должны дублироваться.
    url = hf.build_search_url("dev", schedule="remote", work_format="remote")
    assert _qs(url)["work_format"] == ["REMOTE"]


def test_build_url_salary_and_only_with_salary():
    url = hf.build_search_url("dev", salary_from=150000, only_with_salary=True)
    qs = _qs(url)
    assert qs["salary"] == ["150000"]
    assert qs["currency"] == ["RUR"]
    assert qs["only_with_salary"] == ["true"]


def test_build_url_no_salary_when_zero():
    url = hf.build_search_url("dev", salary_from=0)
    assert "salary=" not in url
    assert "only_with_salary" not in url


def test_build_url_full_combo():
    url = hf.build_search_url(
        "ML инженер",
        region=1,
        period=1,
        schedule="hybrid",
        salary_from=200000,
        only_with_salary="on",
        experience_years=5,
        employment="full",
        work_format="remote",
    )
    qs = _qs(url)
    assert qs["experience"] == ["noExperience", "between1And3", "between3And6"]
    assert set(qs["work_format"]) == {"HYBRID", "REMOTE"}
    assert qs["employment"] == ["full"]
    assert qs["salary"] == ["200000"]
    assert qs["only_with_salary"] == ["true"]
