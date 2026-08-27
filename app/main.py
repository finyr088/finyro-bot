"""Точка входа: FastAPI (API + мини-апп) и Telegram-бот в одном процессе."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .db import init_db
from .api.routes import router as api_router
from .api.admin import router as admin_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("finyro")

WEB_DIR = BASE_DIR / "web"
MEDIA_DIR = Path(settings.MEDIA_DIR)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

_bot_task: asyncio.Task | None = None
_db_ready = asyncio.Event()


async def _run_bot() -> None:
    # Любая ошибка бота логируется, но НЕ влияет на веб-сервер (healthcheck).
    try:
        from aiogram.types import BotCommand

        from .bot.instance import setup_bot

        bot, dp = setup_bot()
        # Ждём готовности БД, чтобы не обрабатывать сообщения до создания таблиц.
        try:
            await asyncio.wait_for(_db_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            log.warning("БД не инициализировалась за 30с — стартую бота всё равно")
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Начать / главное меню"),
                    BotCommand(command="status", description="Мой статус доступа"),
                    BotCommand(command="ref", description="Реферальная программа"),
                    BotCommand(command="admin", description="Админ-панель (для владельца)"),
                ]
            )
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось выполнить предстартовые вызовы бота: %s", exc)
        log.info("Бот запущен в режиме polling")
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Бот остановился с ошибкой (веб-сервер продолжает работать)")


async def _startup_db() -> None:
    try:
        await init_db()
        from .seed import seed_if_empty
        await seed_if_empty()
        # Загружаем редактируемые настройки (цена курса) из БД в рантайм.
        from .db import session_scope
        from . import services
        async with session_scope() as session:
            await services.load_runtime_settings(session)
        log.info("БД инициализирована")
    except Exception:  # noqa: BLE001
        log.exception("Ошибка инициализации БД (сервер продолжит работу)")
    finally:
        _db_ready.set()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    # Инициализацию БД и запуск бота выносим в фоновые задачи, чтобы веб-сервер
    # (и /healthz) поднялся мгновенно и прошёл healthcheck платформы.
    asyncio.create_task(_startup_db())

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
app.include_router(admin_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "bot": settings.bot_configured}


# Загруженные видео (с поддержкой Range-запросов для перемотки).
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


def _asset_ver(name: str) -> int:
    try:
        return int((WEB_DIR / name).stat().st_mtime)
    except OSError:
        return int(time.time())


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def index():
    """Отдаём index.html без кеша и с версиями ассетов — чтобы обновления
    подхватывались сразу (Telegram кеширует мини-аппы очень агрессивно)."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="styles.css"', f'href="styles.css?v={_asset_ver("styles.css")}"')
    html = html.replace('src="app.js"', f'src="app.js?v={_asset_ver("app.js")}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# Статика мини-приложения. Монтируется последней — /api/*, /media/*, / имеют приоритет.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
