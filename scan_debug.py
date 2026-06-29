"""
Визуальная отладка парсинга: скриншоты страниц поиска HH + дамп найденных
карточек (новые / уже в базе). Нужен, чтобы «глазами» увидеть, что именно
видит парсер и почему за день мало/ноль новых вакансий.

Артефакты складываются в ``data/scan_debug/<job_id>/``:
  • ``page_0.png`` … — полностраничные скриншоты выдачи;
  • ``manifest.json`` — что нашли на каждой странице (id/title/seen) и итоги.

Модуль не зависит от веб-слоя: рекордер пишет файлы, а дашборд их читает.
Любые ошибки захвата подавляются — отладка не должна ронять скан.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


def _root() -> Path:
    return Path(config.SCAN_DEBUG_DIR)


def _run_dir(job_id: int | str) -> Path:
    return _root() / str(job_id)


class ScanDebugRecorder:
    """Пишет скриншоты страниц поиска и манифест по одному скану.

    Используется как: создать → ``capture_page`` на каждой странице →
    ``finalize`` в конце. Все методы безопасны: при ошибке логируют и
    продолжают, чтобы не сломать сам скан.
    """

    def __init__(self, job_id: int | str, query: str, *, enabled: bool | None = None) -> None:
        self.job_id = job_id
        self.query = query
        self.enabled = config.SCAN_DEBUG_ENABLED if enabled is None else enabled
        self.dir = _run_dir(job_id)
        self.search_url = ""
        self.pages: list[dict[str, Any]] = []
        self.started_at = time.time()
        self.status = "running"
        self._ready = False

    def _ensure_dir(self) -> bool:
        if self._ready:
            return True
        if not self.enabled:
            return False
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._ready = True
        except Exception:
            log.exception("scan_debug: не удалось создать каталог %s", self.dir)
            self.enabled = False
        return self._ready

    def set_search_url(self, url: str) -> None:
        self.search_url = url
        # Сразу пишем «running»-манифест, чтобы дашборд показал скан вживую,
        # ещё до того как загрузится первая страница выдачи.
        self._write_manifest()

    def _write_manifest(self, extra_totals: dict[str, Any] | None = None) -> None:
        if not self._ensure_dir():
            return
        merged = self.summary()
        if extra_totals:
            merged.update(extra_totals)
        manifest = {
            "job_id": self.job_id,
            "query": self.query,
            "status": self.status,
            "search_url": self.search_url,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)),
            "duration_sec": round(time.time() - self.started_at, 1),
            "pages": self.pages,
            "totals": merged,
        }
        try:
            (self.dir / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            log.exception("scan_debug: не удалось записать манифест")

    async def capture_page(
        self,
        page: Any,
        page_num: int,
        cards: list[dict[str, Any]],
        found_total: int | None = None,
    ) -> None:
        """Скриншот страницы поиска + запись найденных карточек.

        ``cards`` — список dict с ключами id/title/company/salary/seen.
        ``found_total`` — сколько вакансий HH заявил найденными (для диагностики
        «всё ли догрузилось»).
        """
        if not self._ensure_dir():
            return

        shot_name = f"page_{page_num}.png"
        shot_ok = False
        try:
            await page.screenshot(path=str(self.dir / shot_name), full_page=True)
            shot_ok = True
        except Exception:
            log.exception("scan_debug: ошибка скриншота страницы %s", page_num)

        new_cnt = sum(1 for c in cards if not c.get("seen"))
        self.pages.append({
            "page_num": page_num,
            "screenshot": shot_name if shot_ok else "",
            "url": page.url if hasattr(page, "url") else "",
            "found_total": found_total,
            "total": len(cards),
            "new": new_cnt,
            "seen": len(cards) - new_cnt,
            "cards": cards,
        })
        # Обновляем манифест после каждой страницы — дашборд видит прогресс вживую.
        self._write_manifest()

    async def finalize(self, totals: dict[str, Any] | None = None) -> None:
        self.status = "done"
        self._write_manifest(totals)
        _prune_old_runs(config.SCAN_DEBUG_KEEP)

    def summary(self) -> dict[str, Any]:
        """Итоги по уже захваченным страницам (для логов/манифеста)."""
        return {
            "pages": len(self.pages),
            "cards_total": sum(p["total"] for p in self.pages),
            "new_total": sum(p["new"] for p in self.pages),
            "seen_total": sum(p["seen"] for p in self.pages),
        }


# ───────────────────────── чтение для веб-слоя ──────────────────────────────

def list_runs() -> list[dict[str, Any]]:
    """Список прогонов (новые сверху): job_id, query, created_at, итоги."""
    root = _root()
    if not root.exists():
        return []
    runs: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        manifest = entry / MANIFEST_NAME
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        runs.append({
            "job_id": data.get("job_id"),
            "query": data.get("query", ""),
            "created_at": data.get("created_at", ""),
            "totals": data.get("totals", {}),
        })
    runs.sort(key=lambda r: _as_int(r.get("job_id")), reverse=True)
    return runs


def get_run(job_id: int | str) -> dict[str, Any] | None:
    manifest = _run_dir(job_id) / MANIFEST_NAME
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_run() -> dict[str, Any] | None:
    runs = list_runs()
    return get_run(runs[0]["job_id"]) if runs else None


def screenshot_path(job_id: int | str, filename: str) -> Path | None:
    """Безопасный путь к скриншоту (только .png внутри каталога прогона)."""
    name = Path(filename).name  # отсекаем любые ../
    if not name.endswith(".png"):
        return None
    path = _run_dir(job_id) / name
    return path if path.exists() else None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _prune_old_runs(keep: int) -> None:
    """Оставляет только `keep` последних прогонов, остальное удаляет."""
    if keep <= 0:
        return
    root = _root()
    if not root.exists():
        return
    dirs = [d for d in root.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: _as_int(d.name), reverse=True)
    for stale in dirs[keep:]:
        try:
            shutil.rmtree(stale, ignore_errors=True)
        except Exception:
            log.exception("scan_debug: не удалось удалить старый прогон %s", stale)
