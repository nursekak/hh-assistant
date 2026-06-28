"""Сравнение требуемого опыта вакансии с опытом из резюме.

Опыт на HH стандартизирован категориями:
  «Без опыта», «От 1 года до 3 лет», «От 3 до 6 лет», «Более 6 лет».
Здесь мы вытаскиваем минимально требуемый стаж (в годах) и суммарный
стаж кандидата из текста резюме, чтобы отсеять заведомо неподходящие вакансии.
"""

from __future__ import annotations

import re

_NO_EXP_MARKERS = ("без опыта", "не требуется", "без требований", "опыт не требуется")


def parse_required_years(text: str) -> float | None:
    """Минимальный требуемый стаж (в годах) из строки опыта вакансии.

    Возвращает None, если опыт определить не удалось (тогда вакансию не режем).
    """
    if not text:
        return None
    t = text.strip().lower()

    if any(m in t for m in _NO_EXP_MARKERS):
        return 0.0

    # «От 3 до 6 лет», «От 1 года …» — нижняя граница диапазона
    m = re.search(r"от\s*(\d+)", t)
    if m:
        return float(m.group(1))

    # «Более 6 лет»
    m = re.search(r"(?:более|свыше|от)\s*(\d+)", t)
    if m:
        return float(m.group(1))

    # Любое первое число с единицей «год/лет»
    m = re.search(r"(\d+)\s*(?:год|года|лет)", t)
    if m:
        return float(m.group(1))

    return None


def parse_resume_years(raw_text: str) -> float | None:
    """Суммарный стаж кандидата (в годах) из текста резюме.

    Ищет строку вида «Опыт работы: 2 года 3 месяца». Возвращает None,
    если стаж в тексте не найден.
    """
    if not raw_text:
        return None
    t = raw_text.lower()

    m = re.search(r"опыт\s+работы[:\s]*([^\n]+)", t)
    segment = m.group(1) if m else t[:300]

    years = 0.0
    found = False

    ym = re.search(r"(\d+)\s*(?:год|года|лет)", segment)
    if ym:
        years += float(ym.group(1))
        found = True

    mm = re.search(r"(\d+)\s*мес", segment)
    if mm:
        years += float(mm.group(1)) / 12.0
        found = True

    return round(years, 2) if found else None


def is_experience_ok(
    required_years: float | None,
    resume_years: float | None,
    tolerance: float = 0.5,
) -> bool:
    """True, если стаж кандидата подходит под требования вакансии.

    Если что-то определить не удалось (None) — не отсекаем (возвращаем True),
    чтобы не терять вакансии из-за неполных данных.
    """
    if required_years is None or resume_years is None:
        return True
    return required_years <= resume_years + tolerance
