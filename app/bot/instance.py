"""Синглтоны Bot и Dispatcher. Бот создаётся только если задан валидный BOT_TOKEN."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
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
        _bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        _dp = Dispatcher(storage=MemoryStorage())
        from . import admin, student

        # Порядок важен: админский роутер фильтрует по ADMIN_IDS и идёт первым.
        _dp.include_router(admin.router)
        _dp.include_router(student.router)
    return _bot, _dp
