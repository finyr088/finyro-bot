"""REST API мини-приложения Финуро."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from .. import services
from ..bot.notify import notify_admins
from ..models import (
    Material,
    MaterialKind,
    MaterialStatus,
    Test,
    TestAttempt,
    User,
    utcnow,
)
from ..security import AuthError, create_token, validate_init_data
from .deps import effective_access, get_current_user, require_access

router = APIRouter(prefix="/api")


# --- Схемы запросов/ответов ---

class AuthRequest(BaseModel):
    init_data: str = ""


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


# --- Личный кабинет ---

@router.get("/me")
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    summary = await services.progress_summary(session, user)
    cont = await services.continue_video(session, user) if effective_access(user) else None
    return {
        "user": _user_public(user),
        "progress": summary,
        "continue": cont,
        "course": {"title": settings.COURSE_TITLE, "price": settings.COURSE_PRICE},
    }


# --- Вебинары ---

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
    user: User = Depends(require_access),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None or mat.kind != MaterialKind.VIDEO or not mat.is_visible():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вебинар не найден или ещё не опубликован")
    await services.touch_opened(session, user.id, mat.id)
    await session.commit()
    watched = await services.get_progress(session, user.id, mat.id)
    # Загруженные файлы отдаём по внутреннему пути /media/<файл>; внешние ссылки — как есть.
    stream_url = mat.stream_url or ""
    if stream_url.startswith("upload:"):
        stream_url = "/media/" + stream_url[len("upload:"):]
    return {
        "id": mat.id,
        "title": mat.title,
        "description": mat.description,
        "stream_url": stream_url,
        "watched": bool(watched and watched.watched),
        # Данные для динамического водяного знака поверх видео
        "watermark": f"{user.full_name} · id{user.telegram_id}",
    }


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
