"""
Логика матчинга резюме ↔ вакансия: точное + семантическое сравнение навыков.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import embeddings
from config import (
    EMBED_SEM_THRESHOLD,
    MATCH_WEIGHT_EXACT,
    MATCH_WEIGHT_SEM,
)
from extractor import ResumeProfile, VacancyProfile

log = logging.getLogger(__name__)

STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "от", "до", "из", "к", "о", "об",
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "with",
}


@dataclass
class MatchResult:
    score: float
    score_pct: int
    matched: list[str]
    missing: list[str]
    extra: list[str]
    verdict: str


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _exact_match_sets(vac_skills: list[str], res_skills: list[str]) -> tuple[set[str], set[str], set[str]]:
    vac_set = {_norm(s) for s in vac_skills if _norm(s)}
    res_set = {_norm(s) for s in res_skills if _norm(s)}
    matched = vac_set & res_set
    missing = vac_set - res_set
    extra = res_set - vac_set
    return matched, missing, extra


async def _semantic_match(
    vac_skills: list[str],
    res_skills: list[str],
) -> tuple[set[str], set[str], set[str]]:
    """Для каждого навыка вакансии ищет лучший семантический матч в резюме."""
    if not vac_skills or not res_skills:
        return set(), {_norm(s) for s in vac_skills}, {_norm(s) for s in res_skills}

    try:
        vac_vecs = await embeddings.embed(vac_skills)
        res_vecs = await embeddings.embed(res_skills)
        mat = embeddings.cosine_matrix(np.array(vac_vecs), np.array(res_vecs))

        matched: set[str] = set()
        missing: set[str] = set()
        matched_res_indices: set[int] = set()

        for i, vac_skill in enumerate(vac_skills):
            best_j = int(np.argmax(mat[i]))
            best_score = float(mat[i, best_j])
            if best_score >= EMBED_SEM_THRESHOLD:
                matched.add(_norm(vac_skill))
                matched_res_indices.add(best_j)
            else:
                missing.add(_norm(vac_skill))

        extra = {_norm(res_skills[j]) for j in range(len(res_skills)) if j not in matched_res_indices}
        return matched, missing, extra
    except Exception:
        log.exception("semantic match failed, falling back to exact only")
        return _exact_match_sets(vac_skills, res_skills)


def _tfidf_fallback_score(vacancy_text: str, resume_text: str) -> float:
    if not vacancy_text or not resume_text:
        return 0.0
    try:
        vectorizer = TfidfVectorizer(
            stop_words=list(STOP_WORDS),
            token_pattern=r"(?u)\b[\w#+]+\b",
        )
        matrix = vectorizer.fit_transform([resume_text, vacancy_text])
        return float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
    except ValueError:
        return 0.0


async def compute_match(
    vacancy: VacancyProfile,
    resume: ResumeProfile,
    vacancy_text: str = "",
    resume_text: str = "",
    threshold: float = 0.65,
) -> MatchResult:
    """
    Считает совпадение вакансии с резюме.
      matched — навыки вакансии, найденные в резюме
      missing — навыки вакансии, отсутствующие в резюме
      extra   — навыки резюме, не требуемые вакансией
    """
    vac_skills = vacancy.all_skills()
    res_skills = resume.all_skills()

    exact_m, exact_miss, exact_extra = _exact_match_sets(vac_skills, res_skills)

    sem_m, sem_miss, sem_extra = await _semantic_match(vac_skills, res_skills)

    matched = sorted(exact_m | sem_m)
    missing = sorted((exact_miss | sem_miss) - set(matched))
    extra = sorted(exact_extra | sem_extra)

    if vac_skills:
        vac_norm = {_norm(s) for s in vac_skills}
        exact_ratio = len(exact_m) / len(vac_norm) if vac_norm else 0.0
        sem_ratio = len(sem_m) / len(vac_norm) if vac_norm else 0.0
        score = MATCH_WEIGHT_EXACT * exact_ratio + MATCH_WEIGHT_SEM * sem_ratio
    else:
        score = _tfidf_fallback_score(vacancy_text, resume_text)

    score_pct = int(round(score * 100))
    verdict = "PASS" if score >= threshold else "SKIP"

    return MatchResult(
        score=score,
        score_pct=score_pct,
        matched=matched,
        missing=missing,
        extra=extra,
        verdict=verdict,
    )


def format_match_line(result: MatchResult, max_items: int = 6) -> str:
    """Форматирует строку совпадения для Telegram."""
    matched_str = " ".join(f"✅ {_esc_display(k)}" for k in result.matched[:max_items])
    missing_str = " ".join(f"❌ {_esc_display(k)}" for k in result.missing[:max_items])
    parts = [f"🎯 Совпадение: {result.score_pct}%"]
    if result.verdict == "SKIP":
        parts.append("   ⚠️ Ниже порога — отклик ограничен")
    if matched_str:
        parts.append(f"   {matched_str}")
    if missing_str:
        parts.append(f"   {missing_str}")
    return "\n".join(parts)


def _esc_display(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
