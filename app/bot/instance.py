"""Синглтоны Bot и Dispatcher. Бот создаётся только если задан валидный BOT_TOKEN."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from ..config import settings

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot | None:
    """Возвращает экземпляр бота или None, если токен не настроен."""
    return _bot


def setup_bot() -> tuple[Bot, Dispatcher]:
    """Создаёт бота, диспетчер и подключает роутеры. Идемпотентно."""
    global _bot, _dp
    if _bot is None:
        # Прокси для Telegram API (если задан) + короткий таймаут, чтобы
        # отправка не висла минуту при плохой связи датацентра с Telegram.
        if settings.TELEGRAM_API_URL:
            session = AiohttpSession(api=TelegramAPIServer.from_base(settings.TELEGRAM_API_URL.rstrip("/")))
        else:
            session = AiohttpSession()
        try:
            session.timeout = 30
        except Exception:  # noqa: BLE001
            pass
        _bot = Bot(
            token=settings.BOT_TOKEN,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        _dp = Dispatcher(storage=MemoryStorage())
        from . import admin, student

        # Порядок важен: админский роутер фильтрует по ADMIN_IDS и идёт первым.
        _dp.include_router(admin.router)
        _dp.include_router(student.router)
    return _bot, _dp
