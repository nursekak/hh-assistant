"""Тесты структур профилей и нормализации в extractor."""

from extractor import ResumeProfile, VacancyProfile, _as_str_list


def test_as_str_list_filters_and_normalizes():
    assert _as_str_list(["Python", " FastAPI ", 123, None, ""]) == ["fastapi", "python"]
    assert _as_str_list("not a list") == []
    assert _as_str_list(None) == []


def test_vacancy_profile_from_dict_normalizes():
    vp = VacancyProfile.from_dict({
        "hard_skills": ["Python", "python", "Docker"],
        "soft_skills": ["Коммуникабельность"],
        "keywords": ["Backend"],
        "experience": ["3+ года"],
    })
    assert "python" in vp.hard_skills
    assert vp.hard_skills.count("python") == 1
    skills = vp.all_skills()
    assert "python" in skills and "docker" in skills and "backend" in skills


def test_vacancy_profile_all_skills_dedup_across_fields():
    vp = VacancyProfile(hard_skills=["python"], soft_skills=[], keywords=["python", "rest"])
    assert sorted(vp.all_skills()) == ["python", "rest"]


def test_resume_profile_roundtrip_json():
    rp = ResumeProfile(skills=["Python"], experience=["4 года"], stack=["Docker", "Redis"])
    raw = rp.to_json()
    rp2 = ResumeProfile.from_json(raw)
    # from_dict нормализует регистр, поэтому сравниваем нормализованные значения
    assert rp2.skills == ["python"]
    assert set(rp2.all_skills()) == {"python", "docker", "redis"}


def test_profile_from_invalid_json_is_empty():
    assert VacancyProfile.from_json("{ broken").all_skills() == []
    assert ResumeProfile.from_json("").all_skills() == []
