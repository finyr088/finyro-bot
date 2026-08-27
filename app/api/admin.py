"""Админ-API мини-приложения. Все эндпоинты защищены проверкой Telegram ID."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import settings
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..bot.notify import send_to_user
from ..bot.instance import get_bot
from ..bot import texts as bot_texts
from ..bot import keyboards as bot_kb
from ..db import get_session
from ..models import (
    Attachment,
    Event,
    Material,
    MaterialKind,
    MaterialStatus,
    Payment,
    Section,
    Topic,
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


async def _notify_referrer(reward: dict | None) -> None:
    """Сообщает пригласившему о начисленной комиссии (если она была)."""
    if not reward:
        return
    await send_to_user(
        reward["referrer_tg"],
        bot_texts.referral_earned_notice(reward["reward"], reward["percent"], reward["earned_total"]),
    )


# ─────────────────────────── Схемы ───────────────────────────

class MaterialIn(BaseModel):
    kind: str = MaterialKind.VIDEO
    title: str
    description: str | None = None
    content: str | None = None
    stream_url: str | None = None
    topic_id: int | None = None


class QuestionIn(BaseModel):
    text: str
    options: list[str]
    correct_index: int = 0


class TestIn(BaseModel):
    title: str
    description: str | None = None
    questions: list[QuestionIn]


class TestImportIn(BaseModel):
    text: str


class PriceIn(BaseModel):
    price_rub: int


class SectionIn(BaseModel):
    title: str


class TopicIn(BaseModel):
    section_id: int
    title: str


class EventIn(BaseModel):
    date: str            # YYYY-MM-DD
    time: str = ""       # HH:MM (необязательно)
    title: str
    description: str | None = None
    kind: str = "event"


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


# ─────────────────────────── Цена курса ──────────────────────

@router.get("/price")
async def get_price():
    return {"price_rub": settings.COURSE_PRICE_RUB, "price_display": settings.COURSE_PRICE}


@router.post("/price")
async def set_price(body: PriceIn, session: AsyncSession = Depends(get_session)):
    if body.price_rub < 0 or body.price_rub > 100_000_000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректная сумма")
    await services.update_course_price(session, body.price_rub)
    await session.commit()
    return {"ok": True, "price_rub": settings.COURSE_PRICE_RUB, "price_display": settings.COURSE_PRICE}


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
        user, reward = await services.approve_payment(session, p, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_GRANTED, reply_markup=bot_kb.open_app_inline())
        await send_to_user(user.telegram_id, bot_texts.main_menu_text(user.full_name, True), reply_markup=bot_kb.main_menu_inline(True))
        await _notify_referrer(reward)
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
    # Сколько людей привёл каждый пользователь (для проверки рефералки).
    ref_counts = dict(
        (await session.execute(
            select(User.referred_by_id, func.count())
            .where(User.referred_by_id.isnot(None))
            .group_by(User.referred_by_id)
        )).all()
    )
    return {"students": [{
        "telegram_id": u.telegram_id,
        "name": u.full_name,
        "username": u.username,
        "has_access": u.has_access(),
        "created_at": u.created_at.isoformat(),
        "referrals": ref_counts.get(u.id, 0),
        "referral_earned": u.referral_earned or 0,
    } for u in rows]}


@router.post("/students/{telegram_id}/{action}")
async def manage_student(telegram_id: int, action: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if action not in ("grant", "revoke"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестное действие")
    user = await services.get_user_by_tg(session, telegram_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    if action == "grant":
        reward = await services.grant_access(session, user, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_GRANTED, reply_markup=bot_kb.open_app_inline())
        await send_to_user(user.telegram_id, bot_texts.main_menu_text(user.full_name, True), reply_markup=bot_kb.main_menu_inline(True))
        await _notify_referrer(reward)
    else:
        await services.revoke_access(session, user, admin.telegram_id)
        await session.commit()
        await send_to_user(user.telegram_id, bot_texts.ACCESS_REVOKED, reply_markup=bot_kb.main_menu_inline(False))
    return {"ok": True, "has_access": user.has_access()}


@router.delete("/students/{telegram_id}")
async def delete_student(telegram_id: int, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Полностью удаляет аккаунт ученика и все его данные."""
    user = await services.get_user_by_tg(session, telegram_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    name = user.full_name
    await services.delete_user(session, user)
    await session.commit()
    return {"ok": True, "name": name}


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
        topic_id=body.topic_id if body.kind == MaterialKind.VIDEO else None,
    )
    session.add(m)
    await session.commit()
    return {"ok": True, "id": m.id}


_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}


@router.post("/materials/upload")
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    topic_id: int | None = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Загрузка видеофайла вебинара. Сохраняется на диск, создаётся черновик."""
    if not title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _VIDEO_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Разрешены видео: mp4, mov, m4v, webm")

    media_dir = Path(settings.MEDIA_DIR)
    media_dir.mkdir(parents=True, exist_ok=True)
    fname = uuid.uuid4().hex + ext
    dest = media_dir / fname
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # по 1 МБ, чтобы не грузить память
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"Файл больше {settings.MAX_UPLOAD_MB} МБ",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка сохранения: {exc}")

    order = (await session.scalar(
        select(func.max(Material.order_index)).where(Material.kind == MaterialKind.VIDEO)
    ) or 0) + 1
    m = Material(
        kind=MaterialKind.VIDEO, title=title.strip(),
        description=description.strip() or None,
        stream_url=f"upload:{fname}", status=MaterialStatus.DRAFT, order_index=order,
        topic_id=topic_id,
    )
    session.add(m)
    await session.commit()
    return {"ok": True, "id": m.id, "size_mb": round(written / 1024 / 1024, 1)}


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
    # Удаляем загруженный видеофайл, если был.
    if m.stream_url and m.stream_url.startswith("upload:"):
        (Path(settings.MEDIA_DIR) / m.stream_url[len("upload:"):]).unlink(missing_ok=True)
    await session.delete(m)
    await session.commit()
    return {"ok": True}


# ─────────────────────────── Вебинары: разделы/темы/вложения ──

@router.get("/sections")
async def list_sections_admin(session: AsyncSession = Depends(get_session)):
    sections = await services.admin_sections(session)
    out = []
    for s in sections:
        n_topics = await session.scalar(select(func.count(Topic.id)).where(Topic.section_id == s.id)) or 0
        out.append({"id": s.id, "title": s.title, "topics": n_topics})
    return {"sections": out}


@router.post("/sections")
async def create_section(body: SectionIn, session: AsyncSession = Depends(get_session)):
    if not body.title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название раздела")
    order = (await session.scalar(select(func.max(Section.order_index))) or 0) + 1
    s = Section(title=body.title.strip(), order_index=order)
    session.add(s)
    await session.commit()
    return {"ok": True, "id": s.id}


@router.delete("/sections/{section_id}")
async def delete_section(section_id: int, session: AsyncSession = Depends(get_session)):
    s = await session.get(Section, section_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздел не найден")
    topic_ids = [t.id for t in await services.admin_topics(session, section_id)]
    if topic_ids:
        await session.execute(update(Material).where(Material.topic_id.in_(topic_ids)).values(topic_id=None))
        await session.execute(Topic.__table__.delete().where(Topic.section_id == section_id))
    await session.delete(s)
    await session.commit()
    return {"ok": True}


@router.get("/sections/{section_id}/topics")
async def list_topics_admin(section_id: int, session: AsyncSession = Depends(get_session)):
    topics = await services.admin_topics(session, section_id)
    out = []
    for t in topics:
        n = await session.scalar(
            select(func.count(Material.id)).where(Material.kind == MaterialKind.VIDEO, Material.topic_id == t.id)
        ) or 0
        out.append({"id": t.id, "title": t.title, "videos": n})
    return {"topics": out}


@router.post("/topics")
async def create_topic(body: TopicIn, session: AsyncSession = Depends(get_session)):
    if not body.title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название темы")
    if await session.get(Section, body.section_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздел не найден")
    order = (await session.scalar(select(func.max(Topic.order_index)).where(Topic.section_id == body.section_id)) or 0) + 1
    t = Topic(section_id=body.section_id, title=body.title.strip(), order_index=order)
    session.add(t)
    await session.commit()
    return {"ok": True, "id": t.id}


@router.delete("/topics/{topic_id}")
async def delete_topic(topic_id: int, session: AsyncSession = Depends(get_session)):
    t = await session.get(Topic, topic_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тема не найдена")
    await session.execute(update(Material).where(Material.topic_id == topic_id).values(topic_id=None))
    await session.delete(t)
    await session.commit()
    return {"ok": True}


@router.get("/topics/{topic_id}/videos")
async def list_topic_videos_admin(topic_id: int, session: AsyncSession = Depends(get_session)):
    vids = await services.admin_topic_videos(session, topic_id)
    out = []
    for v in vids:
        n_att = await session.scalar(select(func.count(Attachment.id)).where(Attachment.material_id == v.id)) or 0
        out.append({"id": v.id, "title": v.title, "status": v.status, "attachments": n_att,
                    "uploaded": bool(v.stream_url and v.stream_url.startswith("upload:"))})
    return {"videos": out}


# --- Вложения к видео ---

@router.get("/attachments")
async def list_attachments(material_id: int, session: AsyncSession = Depends(get_session)):
    return {"attachments": await services.video_attachments(session, material_id)}


@router.post("/attachments")
async def add_attachment(
    material_id: int = Form(...),
    title: str = Form(...),
    kind: str = Form("file"),
    url: str = Form(""),
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
):
    mat = await session.get(Material, material_id)
    if mat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Видео не найдено")
    if not title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название вложения")

    final_url = url.strip()
    if file is not None and file.filename:
        media_dir = Path(settings.MEDIA_DIR)
        media_dir.mkdir(parents=True, exist_ok=True)
        ext = os.path.splitext(file.filename)[1].lower()
        fname = uuid.uuid4().hex + ext
        dest = media_dir / fname
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        written = 0
        try:
            with open(dest, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {settings.MAX_UPLOAD_MB} МБ")
                    out.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        final_url = f"/media/{fname}"
    if not final_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Прикрепите файл или укажите ссылку")

    order = (await session.scalar(select(func.max(Attachment.order_index)).where(Attachment.material_id == material_id)) or 0) + 1
    a = Attachment(material_id=material_id, title=title.strip(), url=final_url, kind=kind or "file", order_index=order)
    session.add(a)
    await session.commit()
    return {"ok": True, "id": a.id}


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: int, session: AsyncSession = Depends(get_session)):
    a = await session.get(Attachment, attachment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вложение не найдено")
    if a.url and a.url.startswith("/media/"):
        (Path(settings.MEDIA_DIR) / a.url[len("/media/"):]).unlink(missing_ok=True)
    await session.delete(a)
    await session.commit()
    return {"ok": True}


# --- Перемещение (ранжирование ↑/↓) ---

def _reorder(siblings: list, item_id: int, direction: str) -> None:
    """Меняет местами элемент с соседом и пересчитывает order_index по позициям."""
    ids = [s.id for s in siblings]
    if item_id not in ids:
        return
    i = ids.index(item_id)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(siblings):
        return  # уже с краю
    siblings[i], siblings[j] = siblings[j], siblings[i]
    for k, s in enumerate(siblings):
        s.order_index = k


@router.post("/sections/{section_id}/move/{direction}")
async def move_section(section_id: int, direction: str, session: AsyncSession = Depends(get_session)):
    _reorder(await services.admin_sections(session), section_id, direction)
    await session.commit()
    return {"ok": True}


@router.post("/topics/{topic_id}/move/{direction}")
async def move_topic(topic_id: int, direction: str, session: AsyncSession = Depends(get_session)):
    t = await session.get(Topic, topic_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тема не найдена")
    _reorder(await services.admin_topics(session, t.section_id), topic_id, direction)
    await session.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/move/{direction}")
async def move_video(video_id: int, direction: str, session: AsyncSession = Depends(get_session)):
    v = await session.get(Material, video_id)
    if v is None or v.topic_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Видео не найдено")
    _reorder(await services.admin_topic_videos(session, v.topic_id), video_id, direction)
    await session.commit()
    return {"ok": True}


@router.post("/attachments/{attachment_id}/move/{direction}")
async def move_attachment(attachment_id: int, direction: str, session: AsyncSession = Depends(get_session)):
    a = await session.get(Attachment, attachment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вложение не найдено")
    sibs = list(await session.scalars(
        select(Attachment).where(Attachment.material_id == a.material_id)
        .order_by(Attachment.order_index.asc(), Attachment.id.asc())
    ))
    _reorder(sibs, attachment_id, direction)
    await session.commit()
    return {"ok": True}


# ─────────────────────────── Расписание ──────────────────────

@router.get("/events")
async def admin_events(session: AsyncSession = Depends(get_session)):
    rows = list(await session.scalars(select(Event).order_by(Event.event_date.asc())))
    return {"events": [{
        "id": e.id, "date": e.event_date.strftime("%Y-%m-%d"),
        "time": e.event_date.strftime("%H:%M"),
        "title": e.title, "description": e.description, "kind": e.kind,
    } for e in rows]}


@router.post("/events")
async def create_event(body: EventIn, session: AsyncSession = Depends(get_session)):
    from datetime import datetime as _dt
    if not body.title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название события")
    try:
        stamp = f"{body.date} {body.time or '00:00'}"
        dt = _dt.strptime(stamp, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверная дата (нужен формат ГГГГ-ММ-ДД)")
    e = Event(event_date=dt, title=body.title.strip(), description=body.description, kind=body.kind or "event")
    session.add(e)
    await session.commit()
    return {"ok": True, "id": e.id}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, session: AsyncSession = Depends(get_session)):
    e = await session.get(Event, event_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Событие не найдено")
    await session.delete(e)
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


@router.post("/tests/import")
async def import_test(body: TestImportIn, session: AsyncSession = Depends(get_session)):
    """Создаёт тест из вставленного текста (см. app/testimport.py)."""
    from ..testimport import TestParseError, parse_test_text

    try:
        parsed = parse_test_text(body.text)
    except TestParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    order = (await session.scalar(select(func.max(Test.order_index))) or 0) + 1
    t = Test(title=parsed["title"], description=parsed["description"],
             status=MaterialStatus.PUBLISHED, order_index=order)
    session.add(t)
    await session.flush()
    for i, q in enumerate(parsed["questions"], start=1):
        session.add(Question(
            test_id=t.id, text=q["text"], options=q["options"],
            correct_index=q["correct_index"], order_index=i,
        ))
    await session.commit()
    return {"ok": True, "id": t.id, "title": t.title, "questions": len(parsed["questions"])}


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
