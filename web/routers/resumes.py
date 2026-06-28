"""CRUD резюме."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import extractor
import scraper
import storage

router = APIRouter(prefix="/resumes", tags=["resumes"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def resumes_page(request: Request):
    resumes = await storage.get_resumes()
    # Топ пропущенных навыков считаем отдельно по каждому резюме
    for r in resumes:
        r["missing_skills"] = await storage.get_analytics_missing_skills(
            limit=20, resume_id=r["id"]
        )
    return templates.TemplateResponse(
        "resumes.html",
        {"request": request, "resumes": resumes, "message": request.query_params.get("msg", "")},
    )


@router.post("/fetch")
async def fetch_resumes():
    if not Path(config.SESSION_FILE).exists():
        return RedirectResponse("/resumes?msg=Нет+сессии+HH.ru", status_code=303)
    try:
        hh_resumes = await scraper.get_my_resumes()
        for r in hh_resumes:
            await storage.save_resume(r.id, r.title)
        return RedirectResponse(f"/resumes?msg=Загружено+{len(hh_resumes)}+резюме", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/resumes?msg=Ошибка:+{e}", status_code=303)


@router.post("/{resume_id}/activate")
async def activate_resume(resume_id: str):
    await storage.set_active_resume(resume_id)
    try:
        model = await storage.get_setting("ollama_model") or None
        data = await scraper.parse_resume_full(resume_id)
        profile = await extractor.extract_resume_profile(data.raw_text, model=model)
        for s in data.skills:
            if s:
                profile.skills.append(s.strip().lower())
        await storage.save_resume(
            data.id, data.title, data.raw_text,
            profile.all_skills(), profile_json=profile.to_json(),
        )
    except Exception:
        pass
    return RedirectResponse("/resumes?msg=Резюме+активировано", status_code=303)


@router.post("/{resume_id}/reparse")
async def reparse_resume(resume_id: str):
    try:
        model = await storage.get_setting("ollama_model") or None
        data = await scraper.parse_resume_full(resume_id)
        profile = await extractor.extract_resume_profile(data.raw_text, model=model)
        for s in data.skills:
            if s:
                profile.skills.append(s.strip().lower())
        await storage.save_resume(
            data.id, data.title, data.raw_text,
            profile.all_skills(), profile_json=profile.to_json(),
        )
        return RedirectResponse("/resumes?msg=Ключевые+слова+обновлены", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/resumes?msg=Ошибка:+{e}", status_code=303)
