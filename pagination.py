"""
Чистая (без Playwright) логика постраничного сбора уникальных вакансий.

Вынесено отдельно, чтобы покрыть тестами без поднятия браузера: модуль не
зависит ни от Playwright, ни от storage — все источники данных инъектируются.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, TypeVar


class HasId(Protocol):
    id: str


T = TypeVar("T", bound=HasId)

FetchPageFn = Callable[[int], Awaitable[list["T"]]]
IsSeenFn = Callable[[str], Awaitable[bool]]


async def collect_unique(
    fetch_page: "FetchPageFn",
    limit: int,
    *,
    is_seen: IsSeenFn | None = None,
    max_pages: int = 20,
) -> tuple[list[T], int]:
    """Листает страницы (0,1,2,…) пока не наберёт `limit` уникальных НОВЫХ элементов.

    `fetch_page(page_num)` возвращает элементы одной страницы (у каждого есть `.id`).
    `is_seen(id)` — если задан, элементы, для которых он вернул True, считаются уже
    виденными и не учитываются в лимите (так число в настройках = количество
    именно новых вакансий, а не карточек на странице).

    Остановка: набран `limit`, пустая страница (конец выдачи) или достигнут
    `max_pages` (защита от бесконечного листания).

    Возвращает (список новых элементов до limit, число посещённых страниц).
    """
    results: list[T] = []
    seen_ids: set[str] = set()
    pages_visited = 0

    for page_num in range(max_pages):
        if len(results) >= limit:
            break

        items = await fetch_page(page_num)
        pages_visited += 1

        if not items:
            break  # конец выдачи

        for item in items:
            if len(results) >= limit:
                break
            if item.id in seen_ids:
                continue  # дубликат между страницами
            seen_ids.add(item.id)
            if is_seen is not None and await is_seen(item.id):
                continue  # уже в базе — не считается новой
            results.append(item)

    return results, pages_visited
