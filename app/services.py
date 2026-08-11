"""Сервисный слой: операции с пользователями, доступом, оплатами, прогрессом.

Функции принимают AsyncSession и НЕ коммитят сами, если не указано иное —
это делает вызывающий код (хендлер бота или роут API), чтобы управлять транзакцией.
Исключение — явно помеченные commit=True операции.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_EPOCH = datetime(1970, 1, 1)

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import settings
from .models import (
    AdminLog,
    Attachment,
    Material,
    MaterialKind,
    MaterialProgress,
    MaterialStatus,
    Payment,
    PaymentStatus,
    Section,
    SupportMessage,
    Test,
    TestAttempt,
    Topic,
    User,
    utcnow,
)


# --- Пользователи ---

async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        session.add(user)
        await session.flush()
    else:
        # Обновляем профиль, если Telegram прислал свежие данные.
        changed = False
        for field, value in (
            ("username", username),
            ("first_name", first_name),
            ("last_name", last_name),
        ):
            if value and getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            await session.flush()
    return user


async def get_user_by_tg(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


# --- Доступ ---

async def grant_access(session: AsyncSession, user: User, admin_id: int) -> None:
    user.access_active = True
    user.access_granted_at = utcnow()
    if settings.ACCESS_DAYS > 0:
        user.access_expires_at = utcnow() + timedelta(days=settings.ACCESS_DAYS)
    else:
        user.access_expires_at = None
    session.add(
        AdminLog(admin_id=admin_id, action="grant_access", target_user_id=user.telegram_id)
    )


async def revoke_access(session: AsyncSession, user: User, admin_id: int) -> None:
    user.access_active = False
    session.add(
        AdminLog(admin_id=admin_id, action="revoke_access", target_user_id=user.telegram_id)
    )


# --- Оплаты ---

async def create_payment(session: AsyncSession, user: User, proof_file_id: str | None) -> Payment:
    payment = Payment(
        user_id=user.id,
        status=PaymentStatus.PENDING,
        proof_file_id=proof_file_id,
        amount=settings.COURSE_PRICE,
    )
    session.add(payment)
    await session.flush()
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    return await session.get(Payment, payment_id)


async def pending_payments(session: AsyncSession, limit: int = 20) -> list[Payment]:
    result = await session.scalars(
        select(Payment)
        .where(Payment.status == PaymentStatus.PENDING)
        .order_by(Payment.created_at.asc())
        .limit(limit)
    )
    return list(result)


async def approve_payment(session: AsyncSession, payment: Payment, admin_id: int) -> User:
    payment.status = PaymentStatus.APPROVED
    payment.reviewed_at = utcnow()
    payment.reviewed_by = admin_id
    user = await session.get(User, payment.user_id)
    await grant_access(session, user, admin_id)
    return user


async def reject_payment(session: AsyncSession, payment: Payment, admin_id: int) -> User:
    payment.status = PaymentStatus.REJECTED
    payment.reviewed_at = utcnow()
    payment.reviewed_by = admin_id
    return await session.get(User, payment.user_id)


async def latest_payment(session: AsyncSession, user: User) -> Payment | None:
    return await session.scalar(
        select(Payment)
        .where(Payment.user_id == user.id)
        .order_by(Payment.created_at.desc())
        .limit(1)
    )


# --- Материалы ---

async def visible_materials(
    session: AsyncSession, kind: str, now: datetime | None = None
) -> list[Material]:
    """Материалы, видимые ученику: опубликованные или запланированные с наступившей датой."""
    now = now or utcnow()
    result = await session.scalars(
        select(Material)
        .where(Material.kind == kind)
        .order_by(Material.order_index.asc(), Material.id.asc())
    )
    return [m for m in result if m.is_visible(now)]


async def all_materials(session: AsyncSession, kind: str | None = None) -> list[Material]:
    stmt = select(Material).order_by(Material.order_index.asc(), Material.id.asc())
    if kind:
        stmt = stmt.where(Material.kind == kind)
    return list(await session.scalars(stmt))


# --- Иерархия вебинаров: разделы → темы → видео → вложения ---

async def _visible_videos_by_topic(session: AsyncSession, now: datetime) -> dict[int, list[Material]]:
    videos = await session.scalars(
        select(Material).where(Material.kind == MaterialKind.VIDEO)
        .order_by(Material.order_index.asc(), Material.id.asc())
    )
    by_topic: dict[int, list[Material]] = {}
    for v in videos:
        if v.topic_id is not None and v.is_visible(now):
            by_topic.setdefault(v.topic_id, []).append(v)
    return by_topic


async def visible_sections(session: AsyncSession) -> list[dict]:
    """Разделы, в которых есть хотя бы одно опубликованное видео."""
    now = utcnow()
    by_topic = await _visible_videos_by_topic(session, now)
    topics = list(await session.scalars(select(Topic)))
    sections = list(await session.scalars(select(Section).order_by(Section.order_index.asc(), Section.id.asc())))
    out = []
    for s in sections:
        s_topics = [t for t in topics if t.section_id == s.id and by_topic.get(t.id)]
        vids = sum(len(by_topic.get(t.id, [])) for t in s_topics)
        if vids:
            out.append({"id": s.id, "title": s.title, "topics": len(s_topics), "videos": vids})
    return out


async def visible_topics(session: AsyncSession, section_id: int) -> list[dict]:
    now = utcnow()
    by_topic = await _visible_videos_by_topic(session, now)
    topics = list(await session.scalars(
        select(Topic).where(Topic.section_id == section_id).order_by(Topic.order_index.asc(), Topic.id.asc())
    ))
    return [
        {"id": t.id, "title": t.title, "videos": len(by_topic.get(t.id, []))}
        for t in topics if by_topic.get(t.id)
    ]


async def visible_topic_videos(session: AsyncSession, topic_id: int, user_id: int) -> list[dict]:
    now = utcnow()
    vids = list(await session.scalars(
        select(Material).where(Material.kind == MaterialKind.VIDEO, Material.topic_id == topic_id)
        .order_by(Material.order_index.asc(), Material.id.asc())
    ))
    watched = await watched_material_ids(session, user_id)
    return [
        {"id": v.id, "title": v.title, "watched": v.id in watched}
        for v in vids if v.is_visible(now)
    ]


async def video_attachments(session: AsyncSession, material_id: int) -> list[dict]:
    rows = await session.scalars(
        select(Attachment).where(Attachment.material_id == material_id)
        .order_by(Attachment.order_index.asc(), Attachment.id.asc())
    )
    return [{"id": a.id, "title": a.title, "url": a.url, "kind": a.kind} for a in rows]


# --- Иерархия для админки (без фильтра по статусу) ---

async def admin_sections(session: AsyncSession) -> list[Section]:
    return list(await session.scalars(select(Section).order_by(Section.order_index.asc(), Section.id.asc())))


async def admin_topics(session: AsyncSession, section_id: int) -> list[Topic]:
    return list(await session.scalars(
        select(Topic).where(Topic.section_id == section_id).order_by(Topic.order_index.asc(), Topic.id.asc())
    ))


async def admin_topic_videos(session: AsyncSession, topic_id: int) -> list[Material]:
    return list(await session.scalars(
        select(Material).where(Material.kind == MaterialKind.VIDEO, Material.topic_id == topic_id)
        .order_by(Material.order_index.asc(), Material.id.asc())
    ))


async def upcoming_materials(session: AsyncSession, kind: str) -> list[Material]:
    """Запланированные, но ещё не наступившие («скоро»)."""
    now = utcnow()
    result = await session.scalars(
        select(Material)
        .where(Material.kind == kind, Material.status == MaterialStatus.SCHEDULED)
        .order_by(Material.order_index.asc())
    )
    return [m for m in result if not m.is_visible(now)]


# --- Прогресс ---

async def get_progress(session: AsyncSession, user_id: int, material_id: int) -> MaterialProgress | None:
    return await session.scalar(
        select(MaterialProgress).where(
            MaterialProgress.user_id == user_id,
            MaterialProgress.material_id == material_id,
        )
    )


async def mark_watched(session: AsyncSession, user_id: int, material_id: int) -> None:
    progress = await get_progress(session, user_id, material_id)
    if progress is None:
        progress = MaterialProgress(user_id=user_id, material_id=material_id)
        session.add(progress)
    progress.watched = True
    progress.watched_at = utcnow()


async def touch_opened(session: AsyncSession, user_id: int, material_id: int) -> None:
    """Фиксирует факт открытия вебинара — для блока «Продолжить просмотр»."""
    progress = await get_progress(session, user_id, material_id)
    if progress is None:
        progress = MaterialProgress(user_id=user_id, material_id=material_id)
        session.add(progress)
    progress.opened_at = utcnow()


async def continue_video(session: AsyncSession, user: User) -> dict | None:
    """Вебинар для блока «Продолжить»: последний открытый/просмотренный,
    иначе — первый доступный. Возвращает None, если вебинаров нет."""
    videos = await visible_materials(session, MaterialKind.VIDEO)
    if not videos:
        return None
    by_id = {m.id: m for m in videos}

    rows = list(
        await session.scalars(
            select(MaterialProgress).where(MaterialProgress.user_id == user.id)
        )
    )
    # Сортируем по времени последнего касания (открытие или просмотр).
    def key(r: MaterialProgress):
        return r.opened_at or r.watched_at or _EPOCH
    rows.sort(key=key, reverse=True)

    chosen = None
    for r in rows:
        if r.material_id in by_id:
            chosen = (by_id[r.material_id], r.watched)
            break
    if chosen is None:
        chosen = (videos[0], False)  # ещё ничего не открывал — предлагаем начать

    mat, watched = chosen
    return {
        "id": mat.id,
        "title": mat.title,
        "description": mat.description,
        "watched": watched,
    }


async def watched_material_ids(session: AsyncSession, user_id: int) -> set[int]:
    result = await session.scalars(
        select(MaterialProgress.material_id).where(
            MaterialProgress.user_id == user_id, MaterialProgress.watched.is_(True)
        )
    )
    return set(result)


async def progress_summary(session: AsyncSession, user: User) -> dict:
    """Сводка для личного кабинета: просмотрено вебинаров, пройдено тестов, средний балл."""
    now = utcnow()
    videos = await visible_materials(session, MaterialKind.VIDEO, now)
    watched = await watched_material_ids(session, user.id)
    watched_count = len({m.id for m in videos} & watched)

    attempts = list(
        await session.scalars(select(TestAttempt).where(TestAttempt.user_id == user.id))
    )
    tests_passed = len({a.test_id for a in attempts})
    avg_percent = 0
    if attempts:
        percents = [
            (a.score / a.total * 100) if a.total else 0 for a in attempts
        ]
        avg_percent = round(sum(percents) / len(percents))

    return {
        "videos_total": len(videos),
        "videos_watched": watched_count,
        "tests_passed": tests_passed,
        "avg_percent": avg_percent,
    }


# --- Тесты ---

async def visible_tests(session: AsyncSession) -> list[Test]:
    return list(
        await session.scalars(
            select(Test)
            .options(selectinload(Test.questions))
            .where(Test.status == MaterialStatus.PUBLISHED)
            .order_by(Test.order_index.asc(), Test.id.asc())
        )
    )


async def get_test_with_questions(session: AsyncSession, test_id: int) -> Test | None:
    return await session.scalar(
        select(Test).options(selectinload(Test.questions)).where(Test.id == test_id)
    )


# --- Поддержка ---

async def add_support_message(
    session: AsyncSession, user: User, text: str, from_admin: bool = False
) -> SupportMessage:
    msg = SupportMessage(user_id=user.id, text=text, from_admin=from_admin)
    session.add(msg)
    await session.flush()
    return msg


# --- Статистика для админки ---

async def counts(session: AsyncSession) -> dict:
    total_users = await session.scalar(select(func.count(User.id))) or 0
    active_users = await session.scalar(
        select(func.count(User.id)).where(User.access_active.is_(True))
    ) or 0
    pending = await session.scalar(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PENDING)
    ) or 0
    return {"total_users": total_users, "active_users": active_users, "pending": pending}


async def active_user_tg_ids(session: AsyncSession) -> list[int]:
    result = await session.scalars(
        select(User.telegram_id).where(User.access_active.is_(True))
    )
    return list(result)


async def log_admin(
    session: AsyncSession, admin_id: int, action: str, target: int | None = None, details: str | None = None
) -> None:
    session.add(
        AdminLog(admin_id=admin_id, action=action, target_user_id=target, details=details)
    )
