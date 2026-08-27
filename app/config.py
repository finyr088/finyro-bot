"""Конфигурация приложения. Все значения читаются из переменных окружения.

Для локального запуска достаточно указать BOT_TOKEN и ADMIN_IDS в файле .env
(см. .env.example). Остальное имеет разумные значения по умолчанию.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Минималистичный загрузчик .env без внешних зависимостей."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Не перетираем то, что уже задано в окружении (приоритет у реального env).
        os.environ.setdefault(key, value)


_load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- Telegram ---
    BOT_TOKEN: str = _get("BOT_TOKEN")
    ADMIN_IDS: list[int] = [
        int(x) for x in _get("ADMIN_IDS", "").replace(" ", "").split(",") if x
    ]
    # Базовый URL Telegram Bot API. Пусто = api.telegram.org напрямую.
    # Если датацентр плохо достукивается до Telegram, указываем сюда адрес
    # прокси (например, Cloudflare Worker), проксирующего api.telegram.org.
    TELEGRAM_API_URL: str = _get("TELEGRAM_API_URL", "")

    # --- Веб / API ---
    HOST: str = _get("HOST", "0.0.0.0")
    PORT: int = _get_int("PORT", 8080)
    # Публичный HTTPS-адрес мини-приложения (обязателен для кнопки Web App в Telegram).
    MINIAPP_URL: str = _get("MINIAPP_URL", "")
    SECRET_KEY: str = _get("SECRET_KEY", "change-me-in-production-please-32chars")
    # Разрешённые источники для CORS (через запятую). * — любой.
    CORS_ORIGINS: str = _get("CORS_ORIGINS", "*")

    # --- База данных ---
    DATABASE_URL: str = _get(
        "DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'finyro.db'}"
    )

    # --- Курс и оплата ---
    COURSE_TITLE: str = _get("COURSE_TITLE", "Курс подготовки к отборочным этапам по финансовой грамотности")
    COURSE_DESCRIPTION: str = _get(
        "COURSE_DESCRIPTION",
        "Пошаговая подготовка к олимпиадам и конкурсам: вебинары, теория и тесты. "
        "Материалы открываются постепенно, чтобы вы шли по программе без перегруза.",
    )
    COURSE_PRICE: str = _get("COURSE_PRICE", "3 490 ₽")
    # Числовая цена курса — база для расчёта реферальной комиссии.
    COURSE_PRICE_RUB: int = _get_int("COURSE_PRICE_RUB", 3490)

    # --- Реферальная программа ---
    # Комиссия пригласившему: % за первого оплатившего реферала и за остальных.
    REFERRAL_PERCENT_FIRST: int = _get_int("REFERRAL_PERCENT_FIRST", 10)
    REFERRAL_PERCENT_REST: int = _get_int("REFERRAL_PERCENT_REST", 15)
    # Юзернейм бота для персональных ссылок. Пусто = определим через get_me().
    BOT_USERNAME: str = _get("BOT_USERNAME", "").lstrip("@")

    # --- Защита от шеринга: «перегрев» аккаунта при одновременном доступе ---
    # Все три редактируются из админки (хранятся в БД, эти значения — стартовые).
    GUARD_ENABLED: bool = _get_bool("GUARD_ENABLED", True)
    GUARD_LOCK_MINUTES: int = _get_int("GUARD_LOCK_MINUTES", 15)
    GUARD_WINDOW_SECONDS: int = _get_int("GUARD_WINDOW_SECONDS", 120)
    PAY_REQUISITES: str = _get(
        "PAY_REQUISITES",
        "Реквизиты для перевода пока не указаны. Напишите в поддержку.",
    )
    # Вход в мини-апп через браузер по коду (вне Telegram). Выключен ради защиты
    # от шеринга: доступ к курсу — только внутри приложения Telegram.
    ALLOW_CODE_LOGIN: bool = _get_bool("ALLOW_CODE_LOGIN", False)

    # Реквизиты оплаты — редактируются из админки (хранятся в БД, это стартовые).
    PAY_CARD: str = _get("PAY_CARD", "")
    PAY_PHONE: str = _get("PAY_PHONE", "")
    PAY_BANK: str = _get("PAY_BANK", "")
    PAY_RECIPIENT: str = _get("PAY_RECIPIENT", "")
    # Срок доступа в днях после подтверждения. 0 = бессрочно.
    ACCESS_DAYS: int = _get_int("ACCESS_DAYS", 0)

    # Папка для загруженных видео и лимит размера одного файла (МБ).
    MEDIA_DIR: str = _get("MEDIA_DIR", str(BASE_DIR / "media"))
    MAX_UPLOAD_MB: int = _get_int("MAX_UPLOAD_MB", 800)

    # --- Режим разработки ---
    # В DEV_MODE мини-апп можно открыть в обычном браузере без Telegram:
    # авторизация подставляет тестового пользователя (см. security.py).
    DEV_MODE: bool = _get_bool("DEV_MODE", False)
    DEV_USER_ID: int = _get_int("DEV_USER_ID", 999000999)

    @property
    def bot_configured(self) -> bool:
        return bool(self.BOT_TOKEN) and ":" in self.BOT_TOKEN

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.ADMIN_IDS


settings = Settings()
