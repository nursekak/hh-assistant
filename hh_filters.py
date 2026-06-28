"""
Чистое построение URL поиска HH.ru с фильтрами (без Playwright и storage).

Вынесено отдельно, чтобы покрыть тестами без браузера. Здесь живёт вся логика
маппинга «человеческих» настроек в параметры HH:

  • опыт кандидата (в годах)      → experience=noExperience/between1And3/…
  • формат работы (удалёнка и т.п.) → work_format=REMOTE/HYBRID/ON_SITE/FIELD_WORK
  • тип занятости                  → employment=full/part/project/…
  • график работы                 → schedule=fullDay/flexible/shift/…
"""

from __future__ import annotations

from urllib.parse import quote_plus

# --- HH enum опыта (порядок по возрастанию требований) ---
EXP_NONE = "noExperience"
EXP_1_3 = "between1And3"
EXP_3_6 = "between3And6"
EXP_6 = "moreThan6"

# Человеческое значение формата → HH work_format
_WORK_FORMAT_MAP = {
    "remote": "REMOTE",
    "удалёнка": "REMOTE",
    "удаленка": "REMOTE",
    "hybrid": "HYBRID",
    "гибрид": "HYBRID",
    "on_site": "ON_SITE",
    "onsite": "ON_SITE",
    "office": "ON_SITE",
    "офис": "ON_SITE",
    "field_work": "FIELD_WORK",
    "field": "FIELD_WORK",
}

# Допустимые значения типа занятости HH
_EMPLOYMENT_ALLOWED = {"full", "part", "project", "volunteer", "probation"}

# Настоящие значения графика работы HH (без remote — он уехал в work_format)
_SCHEDULE_ALLOWED = {"fullDay", "shift", "flexible", "flyInFlyOut"}

_TRUE = ("1", "true", "yes", "on")


def experience_codes(years: float | int | str | None) -> list[str]:
    """Категории HH-опыта, которым удовлетворяет кандидат со стажем `years`.

    Логика «меньше отсеивать»: кандидат подходит вакансии, если его стаж не
    меньше нижней границы требования. Поэтому возвращаем ВСЕ категории до его
    уровня включительно — HH вернёт вакансии, где опыта кандидата достаточно.

      0 лет → [noExperience]
      2 года → [noExperience, between1And3]      (как «1-3 года»)
      5 лет → [noExperience, between1And3, between3And6]
      8 лет → все четыре
    """
    if years is None or years == "":
        return []
    try:
        y = float(years)
    except (TypeError, ValueError):
        return []
    if y < 0:
        return []

    codes = [EXP_NONE]
    if y >= 1:
        codes.append(EXP_1_3)
    if y >= 3:
        codes.append(EXP_3_6)
    if y >= 6:
        codes.append(EXP_6)
    return codes


def work_format_codes(value: str | None) -> list[str]:
    """Список HH work_format из строки (можно несколько через запятую)."""
    out: list[str] = []
    for part in str(value or "").split(","):
        code = _WORK_FORMAT_MAP.get(part.strip().lower())
        if code and code not in out:
            out.append(code)
    return out


def employment_codes(value: str | None) -> list[str]:
    """Список HH employment из строки (можно несколько через запятую)."""
    out: list[str] = []
    for part in str(value or "").split(","):
        p = part.strip()
        if p in _EMPLOYMENT_ALLOWED and p not in out:
            out.append(p)
    return out


def classify_schedule(value: str | None) -> tuple[str, list[str]]:
    """Разводит legacy-настройку «формат работы» на (schedule, [work_format]).

    Раньше в одном селекте были и удалёнка (remote/hybrid — на деле это
    work_format), и график (fullDay/flexible). Здесь remote/hybrid/office
    уезжают в work_format, а настоящие графики остаются в schedule.
    """
    v = (value or "").strip()
    if not v:
        return "", []
    low = v.lower()
    if low in _WORK_FORMAT_MAP:
        return "", [_WORK_FORMAT_MAP[low]]
    if v in _SCHEDULE_ALLOWED:
        return v, []
    return "", []


def build_search_url(
    query: str,
    *,
    region: str | int = "1",
    period: int = 1,
    schedule: str = "",
    salary_from: int = 0,
    only_with_salary: bool | str = False,
    experience_years: float | int | str | None = None,
    employment: str = "",
    work_format: str = "",
    per_page: int = 50,
) -> str:
    """Собирает полный URL поиска вакансий HH.ru со всеми фильтрами."""
    params: list[tuple[str, str]] = [
        ("text", query),
        ("area", str(region)),
        ("order_by", "publication_time"),
        ("search_period", str(period)),
        ("per_page", str(per_page)),
    ]

    for code in experience_codes(experience_years):
        params.append(("experience", code))

    # Legacy «формат работы» из одного селекта разводим корректно.
    sched, wf = classify_schedule(schedule)
    if sched:
        params.append(("schedule", sched))

    # Явный work_format (новое поле) добавляем поверх, без дублей.
    for code in work_format_codes(work_format):
        if code not in wf:
            wf.append(code)
    for code in wf:
        params.append(("work_format", code))

    for code in employment_codes(employment):
        params.append(("employment", code))

    try:
        sal = int(salary_from)
    except (TypeError, ValueError):
        sal = 0
    if sal > 0:
        params.append(("salary", str(sal)))
        params.append(("currency", "RUR"))

    ows = only_with_salary is True or str(only_with_salary).lower() in _TRUE
    if ows:
        params.append(("only_with_salary", "true"))

    query_str = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params)
    return f"https://hh.ru/search/vacancy?{query_str}"
