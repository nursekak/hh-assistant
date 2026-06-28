"""Роуты настроек бота."""

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import SettingsService

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
settings_service = SettingsService()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    view = await settings_service.get_view()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            # Поиск
            "query": view.query,
            "region": view.region,
            "period": view.period,
            "max_vacancies": view.max_vacancies,
            "hh_schedule": view.hh_schedule,
            "salary_from": view.salary_from,
            "only_with_salary": view.only_with_salary,
            # Матчинг
            "threshold_pct": view.threshold_pct,
            "experience_filter": view.experience_filter,
            "experience_tolerance": view.experience_tolerance,
            # ИИ
            "ollama_model": view.ollama_model,
            "cover_letter_backend": view.cover_letter_backend,
            "anthropic_api_key": view.anthropic_api_key,
            "anthropic_model": view.anthropic_model,
            "candidate_name": view.candidate_name,
            # Расписание
            "interval": view.interval,
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
    candidate_name: str = Form(""),
    # Расписание
    interval: int = Form(2),
):
    interval = await settings_service.save({
        "query": query,
        "region": region,
        "period": period,
        "max_vacancies": max_vacancies,
        "hh_schedule": hh_schedule,
        "salary_from": salary_from,
        "only_with_salary": only_with_salary,
        "threshold_pct": threshold_pct,
        "experience_filter": experience_filter,
        "experience_tolerance": experience_tolerance,
        "ollama_model": ollama_model,
        "cover_letter_backend": cover_letter_backend,
        "anthropic_api_key": anthropic_api_key,
        "anthropic_model": anthropic_model,
        "candidate_name": candidate_name,
        "interval": interval,
    })
    try:
        import bot
        bot.reschedule_scan(interval)
    except Exception:
        pass

    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/reset")
async def settings_reset():
    """Сбрасывает всю статистику (вакансии/аналитику). Резюме сохраняются."""
    deleted = await settings_service.reset_statistics()
    return RedirectResponse(f"/settings?reset={deleted}", status_code=303)
