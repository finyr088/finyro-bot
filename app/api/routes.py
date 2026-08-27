"""REST API мини-приложения Финуро."""
from __future__ import annotations

import random
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from .. import services
from ..bot.notify import notify_admins, send_to_user
from ..models import (
    Event,
    LoginCode,
    Material,
    MaterialKind,
    MaterialStatus,
    Section,
    Test,
    TestAttempt,
    Topic,
    User,
    utcnow,
)
from ..security import AuthError, create_token, validate_init_data
from .deps import effective_access, get_current_user, require_access

router = APIRouter(prefix="/api")


# --- Схемы запросов/ответов ---

class AuthRequest(BaseModel):
    init_data: str = ""


class RequestCodeIn(BaseModel):
    login: str


class VerifyCodeIn(BaseModel):
    login: str
    code: str


CODE_TTL_SECONDS = 600          # код действует 10 минут
CODE_RESEND_COOLDOWN = 60       # не чаще раза в минуту
CODE_MAX_ATTEMPTS = 5


class SupportRequest(BaseModel):
    text: str


class SubmitRequest(BaseModel):
    answers: list[int]


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "name": user.full_name,
        "username": user.username,
        "has_access": effective_access(user),
        "is_admin": settings.is_admin(user.telegram_id),
        "access_expires_at": user.access_expires_at.isoformat() if user.access_expires_at else None,
    }


# --- Авторизация ---

@router.post("/auth/telegram")
async def auth_telegram(body: AuthRequest, session: AsyncSession = Depends(get_session)):
    try:
        data = validate_init_data(body.init_data)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    user = await services.get_or_create_user(
        session,
        telegram_id=data["telegram_id"],
        username=data.get("username"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
    )
    await session.commit()
    token = create_token(user.id, user.telegram_id)
    return {"token": token, "user": _user_public(user)}


# --- Вход по коду (для браузера, когда Telegram недоступен) ---

async def _find_user_by_login(session: AsyncSession, login: str) -> User | None:
    login = (login or "").strip().lstrip("@")
    if not login:
        return None
    if login.isdigit():
        return await session.scalar(select(User).where(User.telegram_id == int(login)))
    return await session.scalar(select(User).where(func.lower(User.username) == login.lower()))


@router.post("/auth/request_code")
async def request_code(body: RequestCodeIn, session: AsyncSession = Depends(get_session)):
    user = await _find_user_by_login(session, body.login)
    if user is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Пользователь не найден. Откройте бота @finyrobot, нажмите «Start» и попробуйте снова.",
        )
    # Антиспам: не чаще раза в минуту.
    recent = await session.scalar(
        select(LoginCode).where(LoginCode.user_id == user.id).order_by(LoginCode.created_at.desc()).limit(1)
    )
    if recent and (utcnow() - recent.created_at).total_seconds() < CODE_RESEND_COOLDOWN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Код уже отправлен в Telegram. Подождите минуту.")

    # Удаляем прежние коды пользователя.
    for old in await session.scalars(select(LoginCode).where(LoginCode.user_id == user.id)):
        await session.delete(old)

    code = f"{random.randint(0, 999999):06d}"
    session.add(LoginCode(user_id=user.id, code=code, expires_at=utcnow() + timedelta(seconds=CODE_TTL_SECONDS)))
    await session.commit()

    ok = await send_to_user(
        user.telegram_id,
        f"🔑 Код для входа на сайт Финуро: <b>{code}</b>\n\n"
        f"Действует 10 минут. Никому не сообщайте этот код.",
    )
    if not ok:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось отправить код в Telegram. Откройте бота @finyrobot и нажмите «Start».",
        )
    return {"ok": True}


@router.post("/auth/verify_code")
async def verify_code(body: VerifyCodeIn, session: AsyncSession = Depends(get_session)):
    user = await _find_user_by_login(session, body.login)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    lc = await session.scalar(
        select(LoginCode).where(LoginCode.user_id == user.id).order_by(LoginCode.created_at.desc()).limit(1)
    )
    if lc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Сначала запросите код")
    if lc.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код истёк — запросите новый")
    if lc.attempts >= CODE_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток — запросите новый код")
    if body.code.strip() != lc.code:
        lc.attempts += 1
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")

    await session.delete(lc)
    await session.commit()
    token = create_token(user.id, user.telegram_id)
    return {"token": token, "user": _user_public(user)}


# --- Личный кабинет ---

def _client_ip(request: Request) -> str:
    """Реальный IP клиента: первый адрес из X-Forwarded-For (его ставит Caddy)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _lock_message(seconds_left: int) -> str:
    mins = max(1, (seconds_left + 59) // 60)
    return (
        "⛔ Доступ к видео временно приостановлен: замечен вход с другого устройства. "
        f"Смотреть можно только на одном устройстве. Попробуйте снова через ~{mins} мин."
    )


@router.get("/me")
async def me(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    summary = await services.progress_summary(session, user)
    has = effective_access(user)
    streak = await services.touch_activity(session, user)
    summary["course_percent"] = await services.course_progress(session, user) if has else 0
    cont = await services.continue_video(session, user) if has else None
    badges = await services.badges(session, user) if has else []
    await services.record_access(session, user.id, _client_ip(request), request.headers.get("user-agent"))
    await session.commit()  # сохраняем обновлённый стрик и отпечаток доступа
    return {
        "user": _user_public(user),
        "progress": summary,
        "streak": streak,
        "badges": badges,
        "continue": cont,
        "course": {"title": settings.COURSE_TITLE, "price": settings.COURSE_PRICE},
    }


# --- Вебинары: разделы → темы → видео ---

@router.get("/sections")
async def list_sections(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    return {"sections": await services.visible_sections(session)}


@router.get("/sections/{section_id}/topics")
async def section_topics(section_id: int, user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздел не найден")
    return {"section": {"id": section.id, "title": section.title},
            "topics": await services.visible_topics(session, section_id)}


@router.get("/topics/{topic_id}/videos")
async def topic_videos(topic_id: int, user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тема не найдена")
    return {"topic": {"id": topic.id, "title": topic.title, "section_id": topic.section_id},
            "videos": await services.visible_topic_videos(session, topic_id, user.id)}


# --- Вебинары (плоский список — для «продолжить» и «новое» на главной) ---

@router.get("/videos")
async def list_videos(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    now = utcnow()
    videos = await services.visible_materials(session, MaterialKind.VIDEO, now)
    upcoming = await services.upcoming_materials(session, MaterialKind.VIDEO)
    watched = await services.watched_material_ids(session, user.id)
    return {
        "videos": [
            {
                "id": m.id,
                "title": m.title,
                "description": m.description,
                "watched": m.id in watched,
            }
            for m in videos
        ],
        "upcoming": [
            {
                "id": m.id,
                "title": m.title,
                "publish_at": m.publish_at.isoformat() if m.publish_at else None,
            }
            for m in upcoming
        ],
    }


@router.get("/videos/{material_id}")
async def get_video(
    material_id: int,
    request: Request,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None or mat.kind != MaterialKind.VIDEO or not mat.is_visible():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вебинар не найден или ещё не опубликован")
    guard = await services.guard_check(session, user, request.headers.get("x-device-id"))
    await services.touch_opened(session, user.id, mat.id)
    await services.record_access(session, user.id, _client_ip(request), request.headers.get("user-agent"))
    await session.commit()
    if guard["locked"]:
        raise HTTPException(status.HTTP_423_LOCKED, _lock_message(guard["seconds_left"]))
    watched = await services.get_progress(session, user.id, mat.id)
    # Загруженные файлы отдаём по внутреннему пути /media/<файл>; внешние ссылки — как есть.
    stream_url = mat.stream_url or ""
    if stream_url.startswith("upload:"):
        stream_url = "/media/" + stream_url[len("upload:"):]
    return {
        "id": mat.id,
        "title": mat.title,
        "description": mat.description,   # комментарий к видео
        "stream_url": stream_url,
        "watched": bool(watched and watched.watched),
        "position": watched.position if watched else 0,
        "attachments": await services.video_attachments(session, mat.id),
        # Данные для динамического водяного знака поверх видео
        "watermark": f"{user.full_name} · id{user.telegram_id}",
    }


class PositionIn(BaseModel):
    position: int = 0


@router.post("/videos/{material_id}/progress")
async def save_video_position(
    material_id: int,
    body: PositionIn,
    request: Request,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None or mat.kind != MaterialKind.VIDEO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вебинар не найден")
    guard = await services.guard_check(session, user, request.headers.get("x-device-id"))
    if not guard["locked"]:
        await services.save_position(session, user.id, material_id, body.position)
    await session.commit()
    if guard["locked"]:
        raise HTTPException(status.HTTP_423_LOCKED, _lock_message(guard["seconds_left"]))
    return {"ok": True}


@router.post("/videos/{material_id}/watch")
async def mark_watched(
    material_id: int,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None or mat.kind != MaterialKind.VIDEO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вебинар не найден")
    await services.mark_watched(session, user.id, material_id)
    await session.commit()
    return {"ok": True}


# --- Теория ---

@router.get("/theory")
async def list_theory(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    topics = await services.visible_materials(session, MaterialKind.THEORY)
    return {"topics": [{"id": t.id, "title": t.title} for t in topics]}


@router.get("/theory/{material_id}")
async def get_theory(
    material_id: int,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None or mat.kind != MaterialKind.THEORY or not mat.is_visible():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тема не найдена")
    return {"id": mat.id, "title": mat.title, "content": mat.content or ""}


# --- Рейтинг активных ---

@router.get("/leaderboard")
async def leaderboard(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    board = await services.leaderboard(session)
    me_row = next((r for r in board if r["user_id"] == user.id), None)
    top = [
        {"rank": r["rank"], "name": r["name"], "points": r["points"],
         "videos": r["videos"], "tests": r["tests"], "is_me": r["user_id"] == user.id}
        for r in board[:20]
    ]
    return {
        "leaderboard": top,
        "me": {"rank": me_row["rank"], "points": me_row["points"]} if me_row else {"rank": None, "points": 0},
        "total": len(board),
    }


# --- Календарь / расписание ---

@router.get("/events")
async def list_events(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select as _sel
    rows = list(await session.scalars(_sel(Event).order_by(Event.event_date.asc())))
    return {
        "events": [
            {
                "id": e.id,
                "date": e.event_date.strftime("%Y-%m-%d"),
                "datetime": e.event_date.isoformat(),
                "title": e.title,
                "description": e.description,
                "kind": e.kind,
            }
            for e in rows
        ]
    }


# --- Тесты ---

@router.get("/tests")
async def list_tests(user: User = Depends(require_access), session: AsyncSession = Depends(get_session)):
    tests = await services.visible_tests(session)
    return {
        "tests": [
            {"id": t.id, "title": t.title, "description": t.description, "questions": len(t.questions)}
            for t in tests
        ]
    }


@router.get("/tests/{test_id}")
async def get_test(
    test_id: int,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    test = await services.get_test_with_questions(session, test_id)
    if test is None or test.status != MaterialStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тест не найден")
    return {
        "id": test.id,
        "title": test.title,
        "description": test.description,
        # Правильные ответы клиенту НЕ отдаём.
        "questions": [
            {"id": q.id, "text": q.text, "options": q.options}
            for q in test.questions
        ],
    }


@router.post("/tests/{test_id}/submit")
async def submit_test(
    test_id: int,
    body: SubmitRequest,
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    test = await services.get_test_with_questions(session, test_id)
    if test is None or test.status != MaterialStatus.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тест не найден")
    questions = test.questions
    if len(body.answers) != len(questions):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Количество ответов не совпадает с числом вопросов")

    correct = [q.correct_index for q in questions]
    score = sum(1 for given, right in zip(body.answers, correct) if given == right)
    total = len(questions)

    attempt = TestAttempt(
        user_id=user.id, test_id=test.id, score=score, total=total, answers=body.answers
    )
    session.add(attempt)
    await session.commit()

    return {
        "score": score,
        "total": total,
        "percent": round(score / total * 100) if total else 0,
        "correct": correct,
        "your": body.answers,
    }


# --- Поддержка ---

@router.post("/support")
async def support(
    body: SupportRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустое сообщение")
    await services.add_support_message(session, user, text, from_admin=False)
    await session.commit()
    await notify_admins(
        f"✉️ <b>Обращение из приложения</b>\n"
        f"От: {user.full_name} (<code>{user.telegram_id}</code>)\n\n"
        f"{text}\n\n"
        f"<i>Ответьте reply на это сообщение, чтобы написать ученику.</i>\n"
        f"#u{user.telegram_id}"
    )
    return {"ok": True}
