"""Telegram presentation helpers (HTML, keyboards)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import matcher
import scraper


def esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_vacancy_keyboard(
    vacancy: scraper.VacancyData,
    match_result: matcher.MatchResult | None,
) -> InlineKeyboardMarkup:
    if match_result and match_result.verdict == "PASS":
        action_row = [
            InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply:{vacancy.id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{vacancy.id}"),
        ]
    elif match_result:
        action_row = [
            InlineKeyboardButton(
                text="⚠️ Откликнуться всё равно",
                callback_data=f"apply_force:{vacancy.id}",
            ),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{vacancy.id}"),
        ]
    else:
        action_row = [
            InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply:{vacancy.id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{vacancy.id}"),
        ]
    return InlineKeyboardMarkup(inline_keyboard=[
        action_row,
        [InlineKeyboardButton(text="🔗 Открыть", url=vacancy.url)],
    ])


def build_letter_preview_keyboard(vacancy_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отклик", callback_data=f"letter_send:{vacancy_id}")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"letter_edit:{vacancy_id}")],
        [InlineKeyboardButton(text="🚫 Без письма", callback_data=f"letter_skip:{vacancy_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"letter_cancel:{vacancy_id}")],
    ])


def format_vacancy_message(
    vacancy: scraper.VacancyData,
    summary: str,
    match_result: matcher.MatchResult | None,
    inject_warn: str = "",
) -> str:
    header = (
        f"🏢 <b>{esc(vacancy.company)}</b>\n"
        f"💼 <b>{esc(vacancy.title)}</b>\n"
        f"💰 {esc(vacancy.salary) or 'зарплата не указана'}\n"
    )
    if match_result:
        header += f"{matcher.format_match_line(match_result)}\n"
    header += f"🔗 <a href='{vacancy.url}'>Открыть вакансию</a>"
    return f"{header}\n\n{esc(summary)}{inject_warn}"
