"""
Асинхронная SQLite-база для хранения вакансий, резюме и настроек.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite

from config import DB_PATH


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in await cur.fetchall()]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vacancies (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                company    TEXT NOT NULL,
                url        TEXT NOT NULL,
                salary     TEXT DEFAULT '',
                status     TEXT DEFAULT 'new',
                summary    TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS resumes (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                raw_text    TEXT DEFAULT '',
                keywords    TEXT DEFAULT '[]',
                is_active   INTEGER DEFAULT 0,
                parsed_at   TIMESTAMP,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS resume_versions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                resume_id     TEXT NOT NULL,
                version       INTEGER NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                raw_text      TEXT DEFAULT '',
                profile_json  TEXT DEFAULT '',
                keywords      TEXT DEFAULT '[]',
                content_hash  TEXT DEFAULT '',
                is_current    INTEGER DEFAULT 0,
                UNIQUE(resume_id, version)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_resume_versions_resume "
            "ON resume_versions(resume_id, version DESC)"
        )
        for col, defn in [
            ("match_score", "REAL DEFAULT 0"),
            ("matched_skills", "TEXT DEFAULT '[]'"),
            ("missing_skills", "TEXT DEFAULT '[]'"),
            ("extra_skills", "TEXT DEFAULT '[]'"),
            ("profile_json", "TEXT DEFAULT ''"),
            ("cover_letter", "TEXT DEFAULT ''"),
            ("response_received", "INTEGER DEFAULT 0"),
            ("response_at", "TIMESTAMP"),
            ("scan_query", "TEXT DEFAULT ''"),
            ("resume_id", "TEXT DEFAULT ''"),
        ]:
            await _ensure_column(db, "vacancies", col, defn)
        await _ensure_column(db, "resumes", "profile_json", "TEXT DEFAULT ''")
        await _ensure_column(db, "resumes", "active_version", "INTEGER DEFAULT 0")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scan_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                query           TEXT NOT NULL,
                phase           TEXT DEFAULT 'search',
                phase_label     TEXT DEFAULT '',
                status          TEXT DEFAULT 'running',
                started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP,
                total           INTEGER DEFAULT 0,
                processed       INTEGER DEFAULT 0,
                new_count       INTEGER DEFAULT 0,
                skipped_count   INTEGER DEFAULT 0,
                current_title   TEXT DEFAULT '',
                current_company TEXT DEFAULT '',
                error           TEXT DEFAULT '',
                logs            TEXT DEFAULT '[]'
            )
        """)
        for col, defn in [
            ("job_type", "TEXT DEFAULT 'scan'"),
            ("attempts", "INTEGER DEFAULT 0"),
            ("worker_id", "TEXT DEFAULT ''"),
        ]:
            await _ensure_column(db, "scan_jobs", col, defn)
        await db.commit()


async def is_seen(vacancy_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM vacancies WHERE id = ?", (vacancy_id,))
        return await cur.fetchone() is not None


async def save_vacancy(
    vacancy_id: str,
    title: str,
    company: str,
    url: str,
    salary: str = "",
    summary: str = "",
    status: str = "new",
    match_score: float = 0.0,
    matched_skills: Optional[list[str]] = None,
    missing_skills: Optional[list[str]] = None,
    extra_skills: Optional[list[str]] = None,
    profile_json: str = "",
    cover_letter: str = "",
    scan_query: str = "",
    resume_id: str = "",
) -> None:
    matched_json = json.dumps(matched_skills or [], ensure_ascii=False)
    missing_json = json.dumps(missing_skills or [], ensure_ascii=False)
    extra_json = json.dumps(extra_skills or [], ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO vacancies
               (id, title, company, url, salary, summary, status,
                match_score, matched_skills, missing_skills, extra_skills,
                profile_json, cover_letter, scan_query, resume_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                vacancy_id, title, company, url, salary, summary, status,
                match_score, matched_json, missing_json, extra_json,
                profile_json, cover_letter, scan_query, resume_id,
            ),
        )
        await db.commit()


async def update_vacancy_match(
    vacancy_id: str,
    status: str,
    match_score: float,
    matched_skills: list[str],
    missing_skills: list[str],
    extra_skills: Optional[list[str]] = None,
    profile_json: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE vacancies SET status = ?, match_score = ?,
               matched_skills = ?, missing_skills = ?, extra_skills = ?,
               profile_json = ? WHERE id = ?""",
            (
                status,
                match_score,
                json.dumps(matched_skills, ensure_ascii=False),
                json.dumps(missing_skills, ensure_ascii=False),
                json.dumps(extra_skills or [], ensure_ascii=False),
                profile_json,
                vacancy_id,
            ),
        )
        await db.commit()


async def set_cover_letter(vacancy_id: str, text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET cover_letter = ? WHERE id = ?",
            (text, vacancy_id),
        )
        await db.commit()


async def update_status(vacancy_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vacancies SET status = ? WHERE id = ?",
            (status, vacancy_id),
        )
        await db.commit()


async def mark_response_received(vacancy_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE vacancies SET status = 'responded', response_received = 1,
               response_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (vacancy_id,),
        )
        await db.commit()


async def reset_statistics() -> int:
    """Удаляет все вакансии (статистику/аналитику). Резюме и настройки не трогает."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM vacancies")
        count = (await cur.fetchone())[0]
        await db.execute("DELETE FROM vacancies")
        await db.commit()
    return count


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status, COUNT(*) FROM vacancies GROUP BY status")
        rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}


async def get_recent_vacancies(limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, title, company, url, salary, status, match_score, created_at
               FROM vacancies ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


def _parse_vacancy_row(row: dict) -> dict:
    d = dict(row)
    for field in ("matched_skills", "missing_skills", "extra_skills"):
        d[field] = json.loads(d.get(field) or "[]")
    return d


async def get_all_vacancies(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, title, company, url, salary, status, match_score,
                      matched_skills, missing_skills, extra_skills,
                      cover_letter, created_at
               FROM vacancies ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        return [_parse_vacancy_row(dict(r)) for r in rows]


async def get_vacancy(vacancy_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return _parse_vacancy_row(dict(row))


# ---- Resumes ----

async def get_resumes() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM resumes ORDER BY is_active DESC, title ASC"
        )
        rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["keywords"] = json.loads(d.get("keywords") or "[]")
            d["profile"] = json.loads(d.get("profile_json") or "null") if d.get("profile_json") else None
            d["is_active"] = bool(d.get("is_active"))
            result.append(d)
        return result


async def save_resume(
    resume_id: str,
    title: str,
    raw_text: str = "",
    keywords: Optional[list[str]] = None,
    profile_json: str = "",
    parsed_at: Optional[str] = None,
) -> None:
    """Сохраняет резюме версионно.

    Таблица resumes — «голова» (актуальный снапшот для matcher/letter/scan).
    Каждое изменение raw_text создаёт неизменяемую строку в resume_versions.
    Повторный парсинг идентичного текста новую версию не плодит (дедуп по hash),
    но обновляет profile_json текущей версии (профиль мог быть переизвлечён).
    """
    kw_json = json.dumps(keywords or [], ensure_ascii=False)
    parsed = parsed_at or datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # Голова существует всегда; при первом сохранении только метаданные.
        await db.execute(
            """INSERT INTO resumes (id, title, raw_text, keywords, profile_json, parsed_at)
               VALUES (?, ?, '', '[]', '', ?)
               ON CONFLICT(id) DO UPDATE SET title = excluded.title""",
            (resume_id, title, parsed),
        )

        # Без текста (например, fetch_from_hh сохраняет только заголовок) —
        # версии не трогаем и не затираем уже сохранённый снапшот.
        if not raw_text:
            await db.commit()
            return

        content_hash = _content_hash(raw_text)
        cur = await db.execute(
            "SELECT id, version, content_hash FROM resume_versions "
            "WHERE resume_id = ? AND is_current = 1",
            (resume_id,),
        )
        current = await cur.fetchone()

        if current and current[2] == content_hash:
            # Текст не изменился — обновляем профиль/ключевые слова текущей версии.
            await db.execute(
                "UPDATE resume_versions SET profile_json = ?, keywords = ? WHERE id = ?",
                (profile_json, kw_json, current[0]),
            )
            active_version = current[1]
        else:
            next_version = (current[1] + 1) if current else 1
            await db.execute(
                "UPDATE resume_versions SET is_current = 0 WHERE resume_id = ?",
                (resume_id,),
            )
            await db.execute(
                """INSERT INTO resume_versions
                   (resume_id, version, created_at, raw_text, profile_json,
                    keywords, content_hash, is_current)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (resume_id, next_version, parsed, raw_text, profile_json, kw_json, content_hash),
            )
            active_version = next_version

        # Зеркалируем актуальный снапшот в голову (backward-compat для читателей).
        await db.execute(
            """UPDATE resumes
               SET title = ?, raw_text = ?, keywords = ?, profile_json = ?,
                   parsed_at = ?, active_version = ?
               WHERE id = ?""",
            (title, raw_text, kw_json, profile_json, parsed, active_version, resume_id),
        )
        await db.commit()


async def set_active_resume(resume_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE resumes SET is_active = 0")
        await db.execute("UPDATE resumes SET is_active = 1 WHERE id = ?", (resume_id,))
        await db.commit()


async def get_active_resume() -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM resumes WHERE is_active = 1 LIMIT 1")
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["keywords"] = json.loads(d.get("keywords") or "[]")
        d["profile"] = json.loads(d.get("profile_json") or "null") if d.get("profile_json") else None
        d["is_active"] = True
        return d


# ---- Resume versions ----

def _version_row_to_dict(row: aiosqlite.Row, with_text: bool = False) -> dict:
    d = dict(row)
    d["keywords"] = json.loads(d.get("keywords") or "[]")
    d["profile"] = json.loads(d.get("profile_json") or "null") if d.get("profile_json") else None
    d["is_current"] = bool(d.get("is_current"))
    if not with_text:
        d.pop("raw_text", None)
    return d


async def get_resume_versions(resume_id: str) -> list[dict]:
    """История версий резюме (без полного текста, новые сверху)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM resume_versions WHERE resume_id = ? ORDER BY version DESC",
            (resume_id,),
        )
        rows = await cur.fetchall()
        return [_version_row_to_dict(r) for r in rows]


async def get_resume_version(resume_id: str, version: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM resume_versions WHERE resume_id = ? AND version = ?",
            (resume_id, version),
        )
        row = await cur.fetchone()
        return _version_row_to_dict(row, with_text=True) if row else None


async def restore_resume_version(resume_id: str, version: int) -> bool:
    """Делает указанную версию текущей и зеркалирует её в голову (rollback)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM resume_versions WHERE resume_id = ? AND version = ?",
            (resume_id, version),
        )
        row = await cur.fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE resume_versions SET is_current = 0 WHERE resume_id = ?",
            (resume_id,),
        )
        await db.execute(
            "UPDATE resume_versions SET is_current = 1 WHERE id = ?",
            (row["id"],),
        )
        await db.execute(
            """UPDATE resumes
               SET raw_text = ?, keywords = ?, profile_json = ?, active_version = ?
               WHERE id = ?""",
            (row["raw_text"], row["keywords"], row["profile_json"], version, resume_id),
        )
        await db.commit()
        return True


# ---- Settings ----

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


async def get_min_match_threshold(default: float = 0.50) -> float:
    val = await get_setting("min_match_threshold", str(default))
    try:
        return float(val)
    except ValueError:
        return default


# ---- Analytics ----

async def get_analytics_funnel() -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM vacancies")
        found = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM vacancies WHERE status != 'auto_skipped'"
        )
        passed_filter = (await cur.fetchone())[0]

        cur = await db.execute(
            """SELECT COUNT(*) FROM vacancies
               WHERE status IN ('shown', 'below_threshold', 'applied', 'skipped', 'responded')"""
        )
        shown = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM vacancies WHERE status IN ('applied', 'responded')"
        )
        applied = (await cur.fetchone())[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM vacancies WHERE status = 'responded'"
        )
        responded = (await cur.fetchone())[0]

        return {
            "found": found,
            "passed_filter": passed_filter,
            "shown": shown,
            "applied": applied,
            "responded": responded,
        }


async def get_analytics_daily(days: int = 14) -> list[dict]:
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT DATE(created_at) AS day,
                      COUNT(*) AS found,
                      SUM(CASE WHEN status IN ('applied','responded') THEN 1 ELSE 0 END) AS applied,
                      SUM(CASE WHEN status = 'responded' THEN 1 ELSE 0 END) AS responded
               FROM vacancies
               WHERE DATE(created_at) >= ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (since,),
        )
        rows = await cur.fetchall()
        return [{"day": r[0], "found": r[1], "applied": r[2], "responded": r[3]} for r in rows]


async def get_analytics_match_histogram(buckets: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT CAST(match_score * 10 AS INTEGER) AS bucket, COUNT(*) AS cnt
               FROM vacancies WHERE match_score > 0
               GROUP BY bucket ORDER BY bucket"""
        )
        rows = await cur.fetchall()
        return [{"bucket": r[0], "count": r[1], "label": f"{r[0]*10}-{(r[0]+1)*10}%"} for r in rows]


async def get_analytics_missing_skills(limit: int = 15, resume_id: Optional[str] = None) -> list[dict]:
    """Топ пропущенных навыков. Если задан resume_id — только по этому резюме."""
    query = "SELECT missing_skills FROM vacancies WHERE missing_skills != '[]'"
    params: tuple = ()
    if resume_id:
        query += " AND resume_id = ?"
        params = (resume_id,)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
    counts: dict[str, int] = {}
    for (raw,) in rows:
        for skill in json.loads(raw or "[]"):
            skill = skill.strip().lower()
            if skill:
                counts[skill] = counts.get(skill, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: -x[1])[:limit]
    return [{"skill": k, "count": v} for k, v in sorted_items]


async def get_analytics_company_conversion(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT company,
                      SUM(CASE WHEN status IN ('applied','responded') THEN 1 ELSE 0 END) AS applied,
                      SUM(CASE WHEN status = 'responded' THEN 1 ELSE 0 END) AS responded
               FROM vacancies
               GROUP BY company
               HAVING applied > 0
               ORDER BY applied DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
    result = []
    for company, applied, responded in rows:
        rate = round(responded / applied * 100, 1) if applied else 0
        result.append({
            "company": company,
            "applied": applied,
            "responded": responded,
            "rate": rate,
        })
    return result


async def get_applied_count_since(days: int) -> int:
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT COUNT(*) FROM vacancies
               WHERE status IN ('applied', 'responded')
               AND DATE(created_at) >= ?""",
            (since,),
        )
        return (await cur.fetchone())[0]


SCAN_LOG_MAX = 40


def _scan_job_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "query": row[1],
        "phase": row[2],
        "phase_label": row[3],
        "status": row[4],
        "started_at": row[5],
        "finished_at": row[6],
        "total": row[7],
        "processed": row[8],
        "new_count": row[9],
        "skipped_count": row[10],
        "current_title": row[11],
        "current_company": row[12],
        "error": row[13],
        "logs": json.loads(row[14] or "[]"),
    }


_SCAN_JOB_SELECT = """
    SELECT id, query, phase, phase_label, status, started_at, finished_at,
           total, processed, new_count, skipped_count,
           current_title, current_company, error, logs
    FROM scan_jobs
"""


async def is_scan_running() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM scan_jobs WHERE status = 'running' LIMIT 1"
        )
        return await cur.fetchone() is not None


async def get_running_scan_job() -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            _SCAN_JOB_SELECT + " WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
    return _scan_job_row_to_dict(row) if row else None


async def get_latest_scan_job() -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            _SCAN_JOB_SELECT + " ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
    return _scan_job_row_to_dict(row) if row else None


async def create_scan_job(query: str, job_type: str = "scan") -> int:
    import time

    started_msg = f"Задача в очереди: «{query}»"
    logs = json.dumps(
        [{"t": time.strftime("%H:%M:%S"), "msg": started_msg}],
        ensure_ascii=False,
    )
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM scan_jobs WHERE status = 'running' LIMIT 1"
        )
        if await cur.fetchone():
            raise RuntimeError("scan_already_running")
        cur = await db.execute(
            """INSERT INTO scan_jobs
               (query, phase, phase_label, status, job_type, logs)
               VALUES (?, 'queued', 'В очереди', 'running', ?, ?)""",
            (query, job_type, logs),
        )
        await db.commit()
        return cur.lastrowid


async def update_scan_job(job_id: int, **fields: Any) -> None:
    allowed = {
        "phase", "phase_label", "status", "total", "processed",
        "new_count", "skipped_count", "current_title", "current_company", "error",
        "job_type", "attempts", "worker_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [job_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE scan_jobs SET {cols} WHERE id = ?", values)
        await db.commit()


async def append_scan_log(job_id: int, message: str) -> None:
    import time

    entry = {"t": time.strftime("%H:%M:%S"), "msg": message}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT logs FROM scan_jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        if not row:
            return
        logs = json.loads(row[0] or "[]")
        logs.append(entry)
        if len(logs) > SCAN_LOG_MAX:
            logs = logs[-SCAN_LOG_MAX:]
        await db.execute(
            "UPDATE scan_jobs SET logs = ? WHERE id = ?",
            (json.dumps(logs, ensure_ascii=False), job_id),
        )
        await db.commit()


async def finish_scan_job(job_id: int, phase: str, label: str) -> None:
    status = "error" if phase == "error" else "done"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE scan_jobs
               SET status = ?, phase = ?, phase_label = ?,
                   finished_at = CURRENT_TIMESTAMP,
                   current_title = '', current_company = ''
               WHERE id = ?""",
            (status, phase, label, job_id),
        )
        await db.commit()


async def reset_orphaned_scan_jobs(reason: str = "Прервано рестартом") -> int:
    """Помечает «зависшие» running-задачи как error.

    Вызывается при старте процессов: после краха воркера/бота running-строка
    осталась бы навсегда и блокировала новые сканы (is_scan_running == True).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """UPDATE scan_jobs
               SET status = 'error', phase = 'error', phase_label = ?,
                   error = ?, finished_at = CURRENT_TIMESTAMP
               WHERE status = 'running'""",
            (reason, reason),
        )
        await db.commit()
        return cur.rowcount
