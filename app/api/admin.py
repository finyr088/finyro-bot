"""Админ-API мини-приложения. Все эндпоинты защищены проверкой Telegram ID."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..bot.notify import send_to_user
from ..bot.instance import get_bot
from ..bot import texts as bot_texts
from ..bot import keyboards as bot_kb
from ..db import get_session
from ..models import (
    Material,
    MaterialKind,
    MaterialStatus,
    Payment,
    PaymentStatus,
    Question,
    SupportMessage,
    Test,
    TestAttempt,
    User,
    utcnow,
)
from .deps import require_admin

log = logging.getLogger("finyro.admin")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


# ─────────────────────────── Схемы ───────────────────────────

class MaterialIn(BaseModel):
    kind: str = MaterialKind.VIDEO
    title: str
    description: str | None = None
    content: str | None = None
    stream_url: str | None = None


class QuestionIn(BaseModel):
    text: str
    options: list[str]
    correct_index: int = 0


class TestIn(BaseModel):
    title: str
    description: str | None = None
    questions: list[QuestionIn]


class ReplyIn(BaseModel):
    text: str


class BroadcastIn(BaseModel):
    text: str
    only_active: bool = True


# ─────────────────────────── Обзор ───────────────────────────

@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    c = await services.counts(session)
    materials = await session.scalar(select(func.count(Material.id))) or 0
    tests = await session.scalar(select(func.count(Test.id))) or 0
    attempts = await session.scalar(select(func.count(TestAttempt.id))) or 0
    return {**c, "materials": materials, "tests": tests, "attempts": attempts}


# ─────────────────────────── Заявки ──────────────────────────

@router.get("/payments")
async def payments(session: AsyncSession = Depends(get_session)):
    items = await services.pending_payments(session, limit=50)
    out = []
    for p in items:
        u = await session.get(User, p.user_id)
        out.append({
            "id": p.id,
            "name": u.full_name if u else f"id{p.user_id}",
            "telegram_id": u.telegram_id if u else None,
            "created_at": p.created_at.isoformat(),
            "amount": p.amount,
            "has_proof": bool(p.proof_file_id),
        })
    return {"payments": out}


@router.get("/payments/{payment_id}/proof")
async def payment_proof(payment_id: int, session: AsyncSession = Depends(get_session)):
    """Проксирует скриншот оплаты из Telegram, чтобы показать его в панели."""
    p = await session.get(Payment, payment_id)
    if p is None or not p.proof_file_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Скриншот не найден")
    bot = get_bot()
    if bot is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Бот недоступен")
    try:
        tg_file = await bot.get_file(p.proof_file_id)
        buf = await bot.download_file(tg_file.file_path)
        return Response(content=buf.read(), media_type="image/jpeg")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Не удалось получить файл: {exc}")


@router.post("/payments/{payment_id}/{action}")
async def review_payment(payment_id: int, action: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if action not in ("approve", "reject"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестное действие")
    p = await services.get_payment(session, payment_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    if p.status != PaymentStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка уже обработана")
    if action == "approve":
        user = await services.approve_payment(session, p, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_GRANTED, reply_markup=bot_kb.open_app_inline())
        await send_to_user(user.telegram_id, "Меню обновлено.", reply_markup=bot_kb.student_menu(True))
    else:
        user = await services.reject_payment(session, p, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.PAYMENT_REJECTED)
    return {"ok": True}


# ─────────────────────────── Ученики ─────────────────────────

@router.get("/students")
async def students(q: str | None = None, session: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(desc(User.created_at)).limit(100)
    rows = list(await session.scalars(stmt))
    if q:
        ql = q.lower()
        rows = [u for u in rows if ql in (u.full_name or "").lower() or ql in str(u.telegram_id)]
    return {"students": [{
        "telegram_id": u.telegram_id,
        "name": u.full_name,
        "username": u.username,
        "has_access": u.has_access(),
        "created_at": u.created_at.isoformat(),
    } for u in rows]}


@router.post("/students/{telegram_id}/{action}")
async def manage_student(telegram_id: int, action: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if action not in ("grant", "revoke"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестное действие")
    user = await services.get_user_by_tg(session, telegram_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    if action == "grant":
        await services.grant_access(session, user, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_GRANTED, reply_markup=bot_kb.open_app_inline())
        await send_to_user(user.telegram_id, "Меню обновлено.", reply_markup=bot_kb.student_menu(True))
    else:
        await services.revoke_access(session, user, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_REVOKED, reply_markup=bot_kb.student_menu(False))
    return {"ok": True, "has_access": user.has_access()}


# ─────────────────────────── Контент ─────────────────────────

@router.get("/materials")
async def list_materials(session: AsyncSession = Depends(get_session)):
    items = await services.all_materials(session)
    return {"materials": [{
        "id": m.id,
        "kind": m.kind,
        "title": m.title,
        "description": m.description,
        "content": m.content,
        "stream_url": m.stream_url,
        "status": m.status,
        "order_index": m.order_index,
    } for m in items]}


@router.post("/materials")
async def create_material(body: MaterialIn, session: AsyncSession = Depends(get_session)):
    if body.kind not in (MaterialKind.VIDEO, MaterialKind.THEORY):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный тип материала")
    if not body.title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой заголовок")
    order = (await session.scalar(
        select(func.max(Material.order_index)).where(Material.kind == body.kind)
    ) or 0) + 1
    m = Material(
        kind=body.kind, title=body.title.strip(),
        description=body.description, content=body.content,
        stream_url=body.stream_url, status=MaterialStatus.DRAFT, order_index=order,
    )
    session.add(m)
    await session.commit()
    return {"ok": True, "id": m.id}


@router.post("/materials/{material_id}/{action}")
async def toggle_material(material_id: int, action: str, session: AsyncSession = Depends(get_session)):
    m = await session.get(Material, material_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Материал не найден")
    if action == "publish":
        m.status = MaterialStatus.PUBLISHED
    elif action == "unpublish":
        m.status = MaterialStatus.DRAFT
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестное действие")
    await session.commit()
    return {"ok": True, "status": m.status}


@router.delete("/materials/{material_id}")
async def delete_material(material_id: int, session: AsyncSession = Depends(get_session)):
    m = await session.get(Material, material_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Материал не найден")
    await session.delete(m)
    await session.commit()
    return {"ok": True}


# ─────────────────────────── Тесты ───────────────────────────

@router.get("/tests")
async def list_tests(session: AsyncSession = Depends(get_session)):
    tests = list(await session.scalars(
        select(Test).order_by(Test.order_index.asc(), Test.id.asc())
    ))
    out = []
    for t in tests:
        n = await session.scalar(select(func.count(Question.id)).where(Question.test_id == t.id)) or 0
        out.append({"id": t.id, "title": t.title, "description": t.description, "status": t.status, "questions": n})
    return {"tests": out}


@router.post("/tests")
async def create_test(body: TestIn, session: AsyncSession = Depends(get_session)):
    if not body.title.strip() or not body.questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужны заголовок и хотя бы один вопрос")
    order = (await session.scalar(select(func.max(Test.order_index))) or 0) + 1
    t = Test(title=body.title.strip(), description=body.description,
             status=MaterialStatus.PUBLISHED, order_index=order)
    session.add(t)
    await session.flush()
    for i, q in enumerate(body.questions, start=1):
        if len(q.options) < 2:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "В вопросе минимум 2 варианта")
        session.add(Question(
            test_id=t.id, text=q.text, options=q.options,
            correct_index=max(0, min(q.correct_index, len(q.options) - 1)), order_index=i,
        ))
    await session.commit()
    return {"ok": True, "id": t.id}


@router.delete("/tests/{test_id}")
async def delete_test(test_id: int, session: AsyncSession = Depends(get_session)):
    t = await session.get(Test, test_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тест не найден")
    await session.delete(t)
    await session.commit()
    return {"ok": True}


# ─────────────────────────── Поддержка ───────────────────────

@router.get("/support")
async def support_list(session: AsyncSession = Depends(get_session)):
    msgs = list(await session.scalars(
        select(SupportMessage).order_by(desc(SupportMessage.created_at)).limit(50)
    ))
    out = []
    for m in msgs:
        u = await session.get(User, m.user_id)
        out.append({
            "id": m.id,
            "name": u.full_name if u else f"id{m.user_id}",
            "telegram_id": u.telegram_id if u else None,
            "from_admin": m.from_admin,
            "text": m.text,
            "created_at": m.created_at.isoformat(),
        })
    return {"messages": out}


@router.post("/support/{telegram_id}/reply")
async def support_reply(telegram_id: int, body: ReplyIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    user = await services.get_user_by_tg(session, telegram_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    if not body.text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой ответ")
    await services.add_support_message(session, user, body.text.strip(), from_admin=True)
    await services.log_admin(session, admin.telegram_id, "support_reply", telegram_id)
    await session.commit()
    ok = await send_to_user(telegram_id, f"💬 <b>Ответ поддержки:</b>\n\n{body.text.strip()}")
    return {"ok": ok}


# ─────────────────────────── Рассылка ────────────────────────

@router.post("/broadcast")
async def broadcast(body: BroadcastIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой текст")
    if body.only_active:
        ids = await services.active_user_tg_ids(session)
    else:
        ids = list(await session.scalars(select(User.telegram_id)))
    await services.log_admin(session, admin.telegram_id, "broadcast", details=text[:200])
    await session.commit()
    sent = 0
    for tg in ids:
        if await send_to_user(tg, f"📢 {text}"):
            sent += 1
    return {"ok": True, "sent": sent, "total": len(ids)}


# ─────────────────────────── Результаты ──────────────────────

@router.get("/results")
async def results(session: AsyncSession = Depends(get_session)):
    attempts = list(await session.scalars(
        select(TestAttempt).order_by(desc(TestAttempt.created_at)).limit(50)
    ))
    out = []
    for a in attempts:
        u = await session.get(User, a.user_id)
        t = await session.get(Test, a.test_id)
        out.append({
            "name": u.full_name if u else f"id{a.user_id}",
            "test": t.title if t else f"тест #{a.test_id}",
            "score": a.score, "total": a.total,
            "percent": round(a.score / a.total * 100) if a.total else 0,
            "created_at": a.created_at.isoformat(),
        })
    return {"results": out}
