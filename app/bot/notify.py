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


async def notify_admins_payment(file_id: str, is_photo: bool, caption: str, reply_markup=None) -> bool:
    """Оповещает админов о новой заявке на оплату. Пытается прикрепить чек
    (фото или файл) с кнопками подтверждения; если не вышло — отправляет текст
    с кнопками, чтобы заявку всегда можно было подтвердить. True — если дошло
    хотя бы до одного админа."""
    bot = get_bot()
    if bot is None:
        log.info("[no-bot] payment notify: %s", caption[:80])
        return False
    if not settings.ADMIN_IDS:
        log.warning("ADMIN_IDS пуст — некому слать заявку на оплату")
        return False

    delivered = False
    for admin_id in settings.ADMIN_IDS:
        sent = False
        try:
            if is_photo:
                await bot.send_photo(admin_id, file_id, caption=caption, reply_markup=reply_markup)
            else:
                await bot.send_document(admin_id, file_id, caption=caption, reply_markup=reply_markup)
            sent = True
        except TelegramAPIError as exc:
            log.warning("Чек админу %s не ушёл (%s) — отправляю текстом", admin_id, exc)
            try:
                await bot.send_message(
                    admin_id,
                    caption + "\n\n📎 Чек не удалось прикрепить — откройте заявку в приложении.",
                    reply_markup=reply_markup,
                )
                sent = True
            except TelegramAPIError as exc2:
                log.warning("Заявка админу %s не доставлена: %s", admin_id, exc2)
        delivered = delivered or sent
    return delivered


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
