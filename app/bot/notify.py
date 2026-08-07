"""Хелперы уведомлений. Безопасны при отсутствии настроенного бота (no-op)."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError

from ..config import settings
from .instance import get_bot

log = logging.getLogger("finyro.notify")


async def notify_admins(text: str, reply_markup=None) -> None:
    bot = get_bot()
    if bot is None:
        log.info("[no-bot] admin notify: %s", text[:80])
        return
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


async def notify_admins_photo(file_id: str, caption: str, reply_markup=None) -> None:
    bot = get_bot()
    if bot is None:
        log.info("[no-bot] admin photo notify: %s", caption[:80])
        return
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=caption, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить фото админу %s: %s", admin_id, exc)


async def send_to_user(telegram_id: int, text: str, reply_markup=None) -> bool:
    bot = get_bot()
    if bot is None:
        log.info("[no-bot] user %s notify: %s", telegram_id, text[:80])
        return False
    try:
        await bot.send_message(telegram_id, text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось написать пользователю %s: %s", telegram_id, exc)
        return False
