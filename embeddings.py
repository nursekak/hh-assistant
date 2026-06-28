"""
Семантические эмбеддинги через Ollama (bge-m3).
"""

from __future__ import annotations

import logging

import httpx
import numpy as np

from config import EMBED_MODEL, OLLAMA_URL

log = logging.getLogger(__name__)

_cache: dict[str, list[float]] = {}


async def embed(texts: list[str]) -> list[list[float]]:
    """Возвращает эмбеддинги для списка текстов."""
    if not texts:
        return []

    missing: list[str] = []
    for t in texts:
        key = t.strip().lower()
        if key and key not in _cache:
            missing.append(t)

    if missing:
        try:
            payload = {"model": EMBED_MODEL, "input": missing}
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/embed", json=payload)
                resp.raise_for_status()
                vectors = resp.json().get("embeddings", [])
            for text, vec in zip(missing, vectors):
                _cache[text.strip().lower()] = vec
        except Exception:
            log.exception("Ollama embed failed")
            raise

    return [_cache[t.strip().lower()] for t in texts if t.strip().lower() in _cache]


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Косинусное сходство между строками a и b."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]))
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a_norm @ b_norm.T


async def is_available() -> bool:
    try:
        vecs = await embed(["test"])
        return len(vecs) == 1 and len(vecs[0]) > 0
    except Exception:
        return False
