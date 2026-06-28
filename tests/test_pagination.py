"""Тесты постраничного сбора уникальных вакансий (pagination.collect_unique)."""

from dataclasses import dataclass

import pytest

import pagination


@dataclass
class Card:
    id: str


def make_pages(*pages: list[str]):
    """Строит fetch_page по списку страниц (каждая — список id).

    Возвращает (fetch_page, requested) где requested — список запрошенных номеров
    страниц, чтобы проверить, что сборщик реально листает дальше.
    """
    requested: list[int] = []

    async def fetch_page(page_num: int) -> list[Card]:
        requested.append(page_num)
        if page_num < len(pages):
            return [Card(i) for i in pages[page_num]]
        return []

    return fetch_page, requested


@pytest.mark.asyncio
async def test_goes_to_next_page_until_limit():
    # На странице 50 карточек, лимит 120 → должен взять 3 страницы.
    fetch, requested = make_pages(
        [str(i) for i in range(0, 50)],
        [str(i) for i in range(50, 100)],
        [str(i) for i in range(100, 150)],
    )
    results, pages_visited = await pagination.collect_unique(fetch, 120, max_pages=20)

    assert len(results) == 120
    assert requested == [0, 1, 2]          # реально пролистал дальше первой страницы
    assert pages_visited == 3


@pytest.mark.asyncio
async def test_stops_on_first_page_when_enough():
    fetch, requested = make_pages([str(i) for i in range(0, 50)])
    results, pages_visited = await pagination.collect_unique(fetch, 20, max_pages=20)

    assert len(results) == 20
    assert requested == [0]                # лимит набран на первой странице
    assert pages_visited == 1


@pytest.mark.asyncio
async def test_stops_on_empty_page():
    # Доступны 2 страницы по 50, лимит 200 → 3-я страница пустая → стоп.
    fetch, requested = make_pages(
        [str(i) for i in range(0, 50)],
        [str(i) for i in range(50, 100)],
    )
    results, pages_visited = await pagination.collect_unique(fetch, 200, max_pages=20)

    assert len(results) == 100
    assert requested == [0, 1, 2]
    assert pages_visited == 3


@pytest.mark.asyncio
async def test_deduplicates_across_pages():
    # Страница 1 повторяет часть id со страницы 0.
    fetch, _ = make_pages(
        ["1", "2", "3"],
        ["3", "4", "5"],
        ["6", "7"],
    )
    results, _ = await pagination.collect_unique(fetch, 100, max_pages=20)

    ids = [c.id for c in results]
    assert ids == ["1", "2", "3", "4", "5", "6", "7"]   # без дублей


@pytest.mark.asyncio
async def test_limit_counts_only_new_vacancies():
    # Уже виденные не должны съедать лимит: нужно набрать именно новых.
    seen = {"1", "2", "3", "4", "5"}

    async def is_seen(vid: str) -> bool:
        return vid in seen

    fetch, requested = make_pages(
        ["1", "2", "3", "4", "5"],         # все виденные
        ["6", "7", "8"],                   # 3 новых
        ["9", "10"],                       # 2 новых
    )
    results, _ = await pagination.collect_unique(fetch, 5, is_seen=is_seen, max_pages=20)

    ids = [c.id for c in results]
    assert ids == ["6", "7", "8", "9", "10"]   # 5 новых, листая дальше виденных
    assert requested == [0, 1, 2]


@pytest.mark.asyncio
async def test_respects_max_pages():
    # Бесконечная выдача, но листаем не больше max_pages.
    async def fetch(page_num: int) -> list[Card]:
        base = page_num * 50
        return [Card(str(base + i)) for i in range(50)]

    results, pages_visited = await pagination.collect_unique(fetch, 10_000, max_pages=3)

    assert pages_visited == 3
    assert len(results) == 150             # 3 страницы * 50
