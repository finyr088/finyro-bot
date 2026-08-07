"""Точка входа: FastAPI (API + мини-апп) и Telegram-бот в одном процессе."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .db import init_db
from .api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("finyro")

WEB_DIR = BASE_DIR / "web"

_bot_task: asyncio.Task | None = None


async def _run_bot() -> None:
    from aiogram.types import BotCommand

    from .bot.instance import setup_bot

    bot, dp = setup_bot()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать / главное меню"),
            BotCommand(command="status", description="Мой статус доступа"),
            BotCommand(command="admin", description="Админ-панель (для владельца)"),
        ]
    )
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Бот запущен в режиме polling")
    await dp.start_polling(bot, handle_signals=False)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    await init_db()
    from .seed import seed_if_empty
    await seed_if_empty()

    if settings.bot_configured:
        _bot_task = asyncio.create_task(_run_bot())
    else:
        log.warning("BOT_TOKEN не задан — бот не запущен, работает только API/мини-апп.")

    try:
        yield
    finally:
        if _bot_task:
            _bot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _bot_task
        from .bot.instance import get_bot
        bot = get_bot()
        if bot:
            await bot.session.close()


app = FastAPI(title="Финуро — курс финграмотности", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "bot": settings.bot_configured}


# Статика мини-приложения. Монтируется последней — /api/* и /healthz имеют приоритет.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
