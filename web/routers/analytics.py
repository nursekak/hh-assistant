"""Аналитика откликов."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
analytics_service = AnalyticsService()


@router.get("", response_class=HTMLResponse)
async def analytics_page(request: Request):
    analytics_data = await analytics_service.get_dashboard_data()

    return templates.TemplateResponse(
        "analytics.html",
        {"request": request, "analytics_data": analytics_data},
    )
