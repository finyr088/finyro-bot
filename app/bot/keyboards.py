"""Клавиатуры бота."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from ..config import settings

# --- Кнопки-подписи (используются и как фильтры входящих сообщений) ---
BTN_PAY = "💳 Оплатить доступ"
BTN_STATUS = "📊 Мой статус"
BTN_SUPPORT = "❓ Поддержка"
BTN_OPEN_APP = "🚀 Открыть приложение"
BTN_CANCEL = "◀️ Отмена"

# Админ-меню
BTN_A_PAYMENTS = "📥 Заявки"
BTN_A_USERS = "👥 Ученики"
BTN_A_CONTENT = "🎬 Контент"
BTN_A_RESULTS = "📊 Результаты"
BTN_A_SUPPORT = "✉️ Обращения"
BTN_A_BROADCAST = "📢 Рассылка"


def _webapp_available() -> bool:
    return settings.MINIAPP_URL.startswith("https://")


def student_menu(has_access: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if has_access and _webapp_available():
        rows.append([KeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=settings.MINIAPP_URL))])
    elif not has_access:
        rows.append([KeyboardButton(text=BTN_PAY)])
    rows.append([KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_SUPPORT)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def open_app_inline() -> InlineKeyboardMarkup | None:
    """Inline-кнопка запуска мини-аппа (для сообщения «Доступ открыт»)."""
    if not _webapp_available():
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=settings.MINIAPP_URL))]]
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True
    )


def main_menu_inline(has_access: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_access:
        # Приложение и сайт — только после подтверждения доступа админом.
        if _webapp_available():
            rows.append([InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url=settings.MINIAPP_URL))])
            rows.append([InlineKeyboardButton(text="🌐 Открыть на сайте (если Telegram тормозит)", url=settings.MINIAPP_URL)])
        rows.append([
            InlineKeyboardButton(text="🎓 О курсе", callback_data="menu:about"),
            InlineKeyboardButton(text="❓ Как учиться", callback_data="menu:how"),
        ])
    else:
        # Ещё не оплатил — никакого доступа к приложению/сайту, только запись.
        rows.append([InlineKeyboardButton(text="🎓 Курс", callback_data="menu:about")])
        rows.append([InlineKeyboardButton(text="✍️ Записаться на курс", callback_data="menu:pay")])
    rows.append([InlineKeyboardButton(text="🎁 Реферальная программа", callback_data="menu:ref")])
    rows.append([
        InlineKeyboardButton(text="📊 Мой статус", callback_data="menu:status"),
        InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="menu:main")]]
    )


def payment_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Отправить чек оплаты", callback_data="pay:send")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="menu:main")],
    ])


def cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="nav:cancel")]]
    )


def referral_inline(link: str, share_text: str, available: int) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(share_text)}"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url)],
    ]
    if available > 0:
        rows.append([InlineKeyboardButton(text=f"💸 Запросить выплату ({available} ₽)", callback_data="ref:payout")])
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def referral_payout_admin(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отметить выплаченным", callback_data=f"refpaid:{telegram_id}")
    ]])


def payment_review(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay:approve:{payment_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay:reject:{payment_id}"),
            ]
        ]
    )


def user_manage(telegram_id: int, has_access: bool) -> InlineKeyboardMarkup:
    if has_access:
        btn = InlineKeyboardButton(text="🔒 Отозвать доступ", callback_data=f"user:revoke:{telegram_id}")
    else:
        btn = InlineKeyboardButton(text="🔓 Выдать доступ", callback_data=f"user:grant:{telegram_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_A_PAYMENTS), KeyboardButton(text=BTN_A_USERS)],
            [KeyboardButton(text=BTN_A_CONTENT), KeyboardButton(text=BTN_A_RESULTS)],
            [KeyboardButton(text=BTN_A_SUPPORT), KeyboardButton(text=BTN_A_BROADCAST)],
        ],
        resize_keyboard=True,
    )


def content_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Вебинар", callback_data="content:add_video")],
            [InlineKeyboardButton(text="➕ Тема теории", callback_data="content:add_theory")],
            [InlineKeyboardButton(text="📋 Список материалов", callback_data="content:list")],
        ]
    )


def material_toggle(material_id: int, published: bool) -> InlineKeyboardMarkup:
    if published:
        btn = InlineKeyboardButton(text="🙈 Снять с публикации", callback_data=f"mat:unpub:{material_id}")
    else:
        btn = InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"mat:pub:{material_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])
