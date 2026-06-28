"""Аналитика откликов."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import storage

router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def analytics_page(request: Request):
    funnel = await storage.get_analytics_funnel()
    daily = await storage.get_analytics_daily(14)
    histogram = await storage.get_analytics_match_histogram()
    companies = await storage.get_analytics_company_conversion()

    # Пропущенные навыки имеют смысл только в привязке к резюме:
    # показываем по активному (если есть), иначе агрегат по всем.
    active = await storage.get_active_resume()
    missing = await storage.get_analytics_missing_skills(
        resume_id=active["id"] if active else None
    )

    analytics_data = {
        "funnel": funnel,
        "daily": daily,
        "histogram": histogram,
        "missing": missing,
        "companies": companies,
        "missing_resume_title": active["title"] if active else None,
    }

    return templates.TemplateResponse(
        "analytics.html",
        {"request": request, "analytics_data": analytics_data},
    )
