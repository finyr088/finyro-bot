"""Авторизация мини-приложения: проверка Telegram WebApp initData и выдача JWT."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

import jwt

from .config import settings

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 дней
# initData считается устаревшей после этого срока (защита от переигрывания).
INIT_DATA_MAX_AGE = 60 * 60 * 24


class AuthError(Exception):
    pass


def validate_init_data(init_data: str) -> dict:
    """Проверяет подпись initData по алгоритму Telegram и возвращает данные пользователя.

    Возвращает dict вида {telegram_id, username, first_name, last_name}.
    Бросает AuthError при некорректной или устаревшей подписи.

    В DEV_MODE пустая строка initData допускается и подставляет тестового пользователя.
    """
    if not init_data:
        if settings.DEV_MODE:
            return {
                "telegram_id": settings.DEV_USER_ID,
                "username": "dev_user",
                "first_name": "Dev",
                "last_name": "Tester",
            }
        raise AuthError("Пустые данные авторизации")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("Отсутствует hash в initData")

    if not settings.bot_configured:
        # Без токена бота проверить подпись невозможно.
        if settings.DEV_MODE:
            user_raw = pairs.get("user", "{}")
            return _extract_user(user_raw)
        raise AuthError("Сервер не настроен для проверки подписи (нет BOT_TOKEN)")

    data_check_string = "\n".join(
        f"{k}={pairs[k]}" for k in sorted(pairs.keys())
    )
    secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise AuthError("Неверная подпись initData")

    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        if time.time() - int(auth_date) > INIT_DATA_MAX_AGE:
            raise AuthError("Данные авторизации устарели, откройте приложение заново")

    return _extract_user(pairs.get("user", "{}"))


def _extract_user(user_raw: str) -> dict:
    try:
        u = json.loads(user_raw)
    except (json.JSONDecodeError, TypeError):
        raise AuthError("Не удалось разобрать данные пользователя")
    if "id" not in u:
        raise AuthError("В данных нет id пользователя")
    return {
        "telegram_id": int(u["id"]),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
    }


def create_token(user_id: int, telegram_id: int) -> str:
    payload = {
        "uid": user_id,
        "tg": telegram_id,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(f"Недействительный токен: {exc}") from exc
