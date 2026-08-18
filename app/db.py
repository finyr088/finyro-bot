"""Асинхронное подключение к БД (SQLAlchemy 2.0) и фабрика сессий."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def _migrate(sync_conn) -> None:
    """Лёгкие аддитивные миграции для SQLite: добавляем недостающие колонки
    в уже существующие таблицы (create_all этого не делает)."""
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    tables = set(insp.get_table_names())
    additions = [
        ("materials", "topic_id", "INTEGER"),
        ("material_progress", "position", "INTEGER"),
        ("users", "streak", "INTEGER"),
        ("users", "last_active_date", "DATE"),
        ("users", "referred_by_id", "INTEGER"),
        ("users", "referral_rewarded", "INTEGER"),
        ("users", "referral_earned", "INTEGER"),
        ("users", "referral_paid_out", "INTEGER"),
    ]
    for table, col, coltype in additions:
        if table not in tables:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if col not in cols:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))


async def init_db() -> None:
    """Создаёт таблицы, которых ещё нет, и добавляет недостающие колонки."""
    from . import models  # noqa: F401  — регистрируем модели в метаданных

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Контекстный менеджер сессии для хендлеров бота и фоновых задач."""
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: одна сессия на запрос."""
    async with SessionLocal() as session:
        yield session
