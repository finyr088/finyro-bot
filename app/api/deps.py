"""Зависимости FastAPI: извлечение текущего пользователя из JWT и проверка доступа."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import User
from ..security import AuthError, decode_token


def effective_access(user: User) -> bool:
    """Доступ к материалам: активная подписка ИЛИ администратор (для превью)."""
    return user.has_access() or settings.is_admin(user.telegram_id)


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нет токена авторизации")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    user = await session.get(User, payload.get("uid"))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    return user


async def require_access(user: User = Depends(get_current_user)) -> User:
    if not effective_access(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к материалам не активирован. Оплатите курс в боте.",
        )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not settings.is_admin(user.telegram_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступ только для администратора")
    return user
