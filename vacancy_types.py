"""Общая модель вакансии для всех источников (HH, Telegram, …)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VacancyData:
    id: str
    title: str
    company: str
    salary: str
    url: str
    full_text: str = field(default="")
    experience: str = field(default="")
    source: str = field(default="hh")
