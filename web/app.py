"""FastAPI веб-интерфейс для управления ботом."""

from pathlib import Path

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web.routers import analytics, resumes, settings
from scan_state import STATE as scan_state

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    app = FastAPI(title="HH Bot Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.include_router(settings.router)
    app.include_router(resumes.router)
    app.include_router(analytics.router)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        import config
        import llm
        import storage

        query = await storage.get_setting("query", config.DEFAULT_QUERY)
        threshold = await storage.get_min_match_threshold(config.MIN_MATCH_THRESHOLD)
        active = await storage.get_active_resume()
        ollama_ok = await llm.check_ollama()
        stats = await storage.get_stats()
        recent = await storage.get_recent_vacancies(5)
        applied_today = await storage.get_applied_count_since(1)
        applied_week = await storage.get_applied_count_since(7)

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "query": query,
                "threshold_pct": int(threshold * 100),
                "active_resume": active,
                "ollama_ok": ollama_ok,
                "session_ok": Path(config.SESSION_FILE).exists(),
                "stats": stats,
                "recent": recent,
                "applied_today": applied_today,
                "applied_week": applied_week,
            },
        )

    @app.get("/api/scan/status")
    async def scan_status():
        return JSONResponse(scan_state.to_dict())

    @app.post("/api/scan/run")
    async def scan_run():
        if scan_state.running:
            return JSONResponse({"ok": False, "reason": "already_running"})
        from bot import run_scan
        asyncio.create_task(run_scan())
        return JSONResponse({"ok": True})

    @app.get("/vacancies", response_class=HTMLResponse)
    async def vacancies_page(request: Request):
        import storage

        vacancies = await storage.get_all_vacancies(200)
        return templates.TemplateResponse(
            "vacancies.html",
            {"request": request, "vacancies": vacancies},
        )

    return app
