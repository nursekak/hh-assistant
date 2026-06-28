"""FastAPI веб-интерфейс для управления ботом."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web.routers import analytics, resumes, settings
from repositories import ScanJobRepository
from services import DashboardService

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    app = FastAPI(title="HH Bot Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    dashboard_service = DashboardService()
    scan_job_repo = ScanJobRepository()

    app.include_router(settings.router)
    app.include_router(resumes.router)
    app.include_router(analytics.router)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        context = await dashboard_service.get_dashboard_context()
        context["request"] = request

        return templates.TemplateResponse(
            "index.html",
            context,
        )

    @app.get("/api/scan/status")
    async def scan_status():
        return JSONResponse(await scan_job_repo.get_status())

    @app.post("/api/scan/run")
    async def scan_run():
        if await scan_job_repo.is_running():
            return JSONResponse({"ok": False, "reason": "already_running"})
        from bot import enqueue_scan
        await enqueue_scan()
        return JSONResponse({"ok": True})

    @app.get("/vacancies", response_class=HTMLResponse)
    async def vacancies_page(request: Request):
        vacancies = await dashboard_service.list_vacancies(200)
        return templates.TemplateResponse(
            "vacancies.html",
            {"request": request, "vacancies": vacancies},
        )

    return app
