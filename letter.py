"""
Генерация сопроводительного письма (Ollama или Claude API).
"""

from dataclasses import asdict
import json
import logging

import httpx

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    COVER_LETTER_BACKEND,
    OLLAMA_MODEL,
    OLLAMA_URL,
)
from extractor import ResumeProfile, VacancyProfile

log = logging.getLogger(__name__)

_SYSTEM = """Ты — карьерный консультант. Пишешь короткие персонализированные сопроводительные письма на русском языке.
Правила:
- 120–180 слов, деловой но живой тон
- Акцентируй навыки из списка «совпавшие», которые важны для вакансии
- Не выдумывай опыт, которого нет в резюме
- Без шаблонных фраз «уверен, что стану ценным сотрудником»
- Верни только текст письма, без заголовков и пояснений"""


async def generate_cover_letter(
    vacancy: VacancyProfile,
    resume: ResumeProfile,
    matched: list[str],
    title: str = "",
    company: str = "",
    model: str = OLLAMA_MODEL,
    backend: str = "",
    api_key: str = "",
) -> str:
    prompt = f"""Напиши сопроводительное письмо для отклика на вакансию.

Компания: {company}
Должность: {title}

Требования вакансии:
{json.dumps(asdict(vacancy), ensure_ascii=False, indent=2)}

Профиль кандидата:
{json.dumps(asdict(resume), ensure_ascii=False, indent=2)}

Совпавшие навыки (обязательно упомяни):
{', '.join(matched) or '—'}"""

    effective_backend = backend or COVER_LETTER_BACKEND
    effective_key = api_key or ANTHROPIC_API_KEY
    if effective_backend == "claude" and effective_key:
        return await _claude_generate(prompt, api_key=effective_key)
    return await _ollama_generate(prompt, model=model)


async def _ollama_generate(prompt: str, model: str = OLLAMA_MODEL) -> str:
    payload = {
        "model": model or OLLAMA_MODEL,
        "system": _SYSTEM,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 500},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception:
        log.exception("Ollama cover letter failed")
        return "Не удалось сгенерировать письмо. Отправьте отклик без сопроводительного."


async def _claude_generate(prompt: str, api_key: str = "") -> str:
    key = api_key or ANTHROPIC_API_KEY
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 600,
                    "system": _SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    except Exception:
        log.exception("Claude cover letter failed")
        return await _ollama_generate(prompt)
