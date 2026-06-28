"""CRUD резюме."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from services import ResumeService

router = APIRouter(prefix="/resumes", tags=["resumes"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
resume_service = ResumeService()


@router.get("", response_class=HTMLResponse)
async def resumes_page(request: Request):
    resumes = await resume_service.list_with_missing_skills()
    return templates.TemplateResponse(
        "resumes.html",
        {"request": request, "resumes": resumes, "message": request.query_params.get("msg", "")},
    )


@router.post("/fetch")
async def fetch_resumes():
    if not Path(config.SESSION_FILE).exists():
        return RedirectResponse("/resumes?msg=Нет+сессии+HH.ru", status_code=303)
    try:
        count = await resume_service.fetch_from_hh()
        return RedirectResponse(f"/resumes?msg=Загружено+{count}+резюме", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/resumes?msg=Ошибка:+{e}", status_code=303)


@router.post("/{resume_id}/activate")
async def activate_resume(resume_id: str):
    try:
        await resume_service.activate(resume_id)
    except Exception:
        pass
    return RedirectResponse("/resumes?msg=Резюме+активировано", status_code=303)


@router.post("/{resume_id}/reparse")
async def reparse_resume(resume_id: str):
    try:
        await resume_service.reparse(resume_id)
        return RedirectResponse("/resumes?msg=Ключевые+слова+обновлены", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/resumes?msg=Ошибка:+{e}", status_code=303)
