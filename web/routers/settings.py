"""Роуты настроек бота."""

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import storage

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    # --- Поиск ---
    query = await storage.get_setting("query", config.DEFAULT_QUERY)
    region = await storage.get_setting("hh_region", config.HH_REGION)
    period = await storage.get_setting("hh_search_period", str(config.HH_SEARCH_PERIOD))
    max_vac = await storage.get_setting("max_vacancies", str(config.MAX_VACANCIES))
    hh_schedule = await storage.get_setting("hh_schedule", "")
    salary_from = await storage.get_setting("salary_from", "0")
    only_with_salary = await storage.get_setting("only_with_salary", "false")

    # --- Матчинг ---
    threshold = await storage.get_min_match_threshold(config.MIN_MATCH_THRESHOLD)
    exp_filter_raw = await storage.get_setting("experience_filter", "")
    exp_filter_on = (
        exp_filter_raw.lower() in ("1", "true", "yes", "on")
        if exp_filter_raw
        else config.EXPERIENCE_FILTER_ENABLED
    )
    try:
        exp_tolerance = float(
            await storage.get_setting("experience_tolerance_years", str(config.EXPERIENCE_TOLERANCE_YEARS))
        )
    except (ValueError, TypeError):
        exp_tolerance = config.EXPERIENCE_TOLERANCE_YEARS

    # --- ИИ-модели ---
    model = await storage.get_setting("ollama_model", config.OLLAMA_MODEL)
    cl_backend = await storage.get_setting("cover_letter_backend", config.COVER_LETTER_BACKEND)
    cl_api_key = await storage.get_setting("anthropic_api_key", "")
    cl_model = await storage.get_setting("anthropic_model", config.ANTHROPIC_MODEL)

    # --- Расписание ---
    interval = await storage.get_setting("scan_interval_hours", str(config.SCAN_INTERVAL_HOURS))

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            # Поиск
            "query": query,
            "region": region,
            "period": int(period),
            "max_vacancies": int(max_vac),
            "hh_schedule": hh_schedule,
            "salary_from": int(salary_from) if salary_from else 0,
            "only_with_salary": only_with_salary.lower() in ("true", "on", "1", "yes"),
            # Матчинг
            "threshold_pct": int(threshold * 100),
            "experience_filter": exp_filter_on,
            "experience_tolerance": exp_tolerance,
            # ИИ
            "ollama_model": model,
            "cover_letter_backend": cl_backend,
            "anthropic_api_key": cl_api_key,
            "anthropic_model": cl_model,
            # Расписание
            "interval": int(interval),
            # Flash
            "saved": request.query_params.get("saved") == "1",
            "reset": request.query_params.get("reset"),
        },
    )


@router.post("/settings")
async def settings_save(
    # Поиск
    query: str = Form(""),
    region: str = Form("1"),
    period: int = Form(1),
    max_vacancies: int = Form(15),
    hh_schedule: str = Form(""),
    salary_from: int = Form(0),
    only_with_salary: str = Form(""),
    # Матчинг
    threshold_pct: int = Form(50),
    experience_filter: str = Form(""),
    experience_tolerance: str = Form("0.5"),
    # ИИ
    ollama_model: str = Form(""),
    cover_letter_backend: str = Form("ollama"),
    anthropic_api_key: str = Form(""),
    anthropic_model: str = Form(""),
    # Расписание
    interval: int = Form(2),
):
    # Поиск
    await storage.set_setting("query", query.strip())
    await storage.set_setting("hh_region", region)
    await storage.set_setting("hh_search_period", str(period))
    await storage.set_setting("max_vacancies", str(max(1, max_vacancies)))
    await storage.set_setting("hh_schedule", hh_schedule)
    await storage.set_setting("salary_from", str(max(0, salary_from)))
    await storage.set_setting("only_with_salary", "true" if only_with_salary else "false")

    # Матчинг
    await storage.set_setting("min_match_threshold", str(threshold_pct / 100))
    await storage.set_setting("experience_filter", "on" if experience_filter else "off")
    try:
        tol = round(float(experience_tolerance), 2)
        await storage.set_setting("experience_tolerance_years", str(tol))
    except (ValueError, TypeError):
        pass

    # ИИ
    if ollama_model.strip():
        await storage.set_setting("ollama_model", ollama_model.strip())
    if cover_letter_backend in ("ollama", "claude"):
        await storage.set_setting("cover_letter_backend", cover_letter_backend)
    if anthropic_api_key.strip():
        await storage.set_setting("anthropic_api_key", anthropic_api_key.strip())
    if anthropic_model.strip():
        await storage.set_setting("anthropic_model", anthropic_model.strip())

    # Расписание
    interval = max(1, interval)
    await storage.set_setting("scan_interval_hours", str(interval))
    try:
        import bot
        bot.reschedule_scan(interval)
    except Exception:
        pass

    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/reset")
async def settings_reset():
    """Сбрасывает всю статистику (вакансии/аналитику). Резюме сохраняются."""
    deleted = await storage.reset_statistics()
    return RedirectResponse(f"/settings?reset={deleted}", status_code=303)
