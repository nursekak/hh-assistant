"""Визуальный просмотр парсинга: скриншоты страниц поиска и найденные карточки."""

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import scan_debug
from repositories import ScanJobRepository

router = APIRouter(tags=["scan-debug"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
scan_job_repo = ScanJobRepository()


@router.get("/scan-preview", response_class=HTMLResponse)
async def scan_preview_page(request: Request):
    runs = scan_debug.list_runs()
    job_id = request.query_params.get("job_id")
    run = scan_debug.get_run(job_id) if job_id else scan_debug.latest_run()

    scan_running = await scan_job_repo.is_running()
    # Живой режим: скан идёт И смотрим именно его (последний, ещё не завершённый).
    live = bool(scan_running and run and run.get("status") == "running" and not job_id)

    return templates.TemplateResponse(
        "scan_preview.html",
        {
            "request": request,
            "runs": runs,
            "run": run,
            "scan_running": scan_running,
            "live": live,
            "cache_bust": int(time.time()),
        },
    )


@router.get("/api/scan/debug/runs")
async def scan_debug_runs():
    return JSONResponse(scan_debug.list_runs())


@router.get("/api/scan/debug/{job_id}")
async def scan_debug_manifest(job_id: int):
    run = scan_debug.get_run(job_id)
    if not run:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(run)


@router.get("/api/scan/debug/{job_id}/shot/{filename}")
async def scan_debug_shot(job_id: int, filename: str):
    path = scan_debug.screenshot_path(job_id, filename)
    if not path:
        return JSONResponse({"error": "not_found"}, status_code=404)
    # no-cache: во время live-скана скриншоты перезаписываются, нельзя кэшировать.
    return FileResponse(
        str(path),
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
