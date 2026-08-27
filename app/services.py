"""Сервисный слой: операции с пользователями, доступом, оплатами, прогрессом.

Функции принимают AsyncSession и НЕ коммитят сами, если не указано иное —
это делает вызывающий код (хендлер бота или роут API), чтобы управлять транзакцией.
Исключение — явно помеченные commit=True операции.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

_EPOCH = datetime(1970, 1, 1)

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import settings
from .models import (
    AdminLog,
    Attachment,
    LoginCode,
    Material,
    MaterialKind,
    MaterialProgress,
    MaterialStatus,
    Payment,
    PaymentStatus,
    AccessFingerprint,
    Section,
    Setting,
    SupportMessage,
    Test,
    TestAttempt,
    Topic,
    User,
    utcnow,
)


# --- Настройки (редактируются из админки) ---

PRICE_KEY = "course_price_rub"


def format_price(rub: int) -> str:
    """3490 → «3 490 ₽» (разделитель тысяч — пробел)."""
    return f"{int(rub):,}".replace(",", " ") + " ₽"


async def get_setting(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(Setting.value).where(Setting.key == key))


async def set_setting(session: AsyncSession, key: str, value) -> None:
    obj = await session.get(Setting, key)
    if obj is None:
        session.add(Setting(key=key, value=str(value)))
    else:
        obj.value = str(value)


def _apply_price(rub: int) -> None:
    """Обновляет цену в рантайме (процесс один — бот и веб видят сразу)."""
    settings.COURSE_PRICE_RUB = rub
    settings.COURSE_PRICE = format_price(rub)


async def load_runtime_settings(session: AsyncSession) -> None:
    """Загружает цену из БД в settings. При первом запуске — инициализирует
    значением из окружения/дефолта."""
    raw = await get_setting(session, PRICE_KEY)
    if raw is None:
        rub = settings.COURSE_PRICE_RUB
        await set_setting(session, PRICE_KEY, rub)
        await session.commit()
    else:
        try:
            rub = int(raw)
        except (TypeError, ValueError):
            rub = settings.COURSE_PRICE_RUB
    _apply_price(rub)


async def update_course_price(session: AsyncSession, rub: int) -> int:
    """Сохраняет новую цену курса и применяет её в рантайме. Возвращает ₽."""
    rub = max(0, int(rub))
    await set_setting(session, PRICE_KEY, rub)
    _apply_price(rub)
    return rub


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


async def delete_user(session: AsyncSession, user: User) -> None:
    """Полностью удаляет аккаунт и все связанные данные — чтобы человек мог
    прийти заново (в т.ч. по реферальной ссылке) как новый пользователь."""
    uid = user.id
    for model in (Payment, TestAttempt, MaterialProgress, SupportMessage, LoginCode, AccessFingerprint):
        await session.execute(delete(model).where(model.user_id == uid))
    # Отвязываем тех, кого этот пользователь пригласил.
    await session.execute(
        update(User).where(User.referred_by_id == uid).values(referred_by_id=None)
    )
    await session.delete(user)


# --- Детекция шеринга аккаунта (один платный доступ на нескольких) ---

def _net16(ip: str) -> str:
    """Грубый идентификатор сети (для оценки «разных мест»)."""
    ip = (ip or "").strip()
    if "." in ip:
        parts = ip.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else ip
    if ":" in ip:
        return ":".join(ip.split(":")[:2])
    return ip or "?"


def device_label(ua: str) -> str:
    """Короткая понятная метка устройства из User-Agent."""
    u = (ua or "").lower()
    if "android" in u:
        os_ = "Android"
    elif "iphone" in u or "ipad" in u or ("like mac" in u and "mobile" in u):
        os_ = "iPhone/iPad"
    elif "windows" in u:
        os_ = "Windows"
    elif "mac os" in u or "macintosh" in u:
        os_ = "Mac"
    elif "linux" in u:
        os_ = "Linux"
    else:
        os_ = "Другое"
    if "telegram" in u:
        return os_ + " · Telegram"
    if "chrome" in u:
        return os_ + " · Chrome"
    if "firefox" in u:
        return os_ + " · Firefox"
    if "safari" in u:
        return os_ + " · Safari"
    return os_


async def record_access(session: AsyncSession, user_id: int, ip: str | None, ua: str | None) -> None:
    """Фиксирует отпечаток (IP + устройство) доступа. Дедуплицирует по паре."""
    ip = (ip or "").strip()[:64] or "unknown"
    device = (ua or "").strip()[:200] or "unknown"
    fp = await session.scalar(select(AccessFingerprint).where(
        AccessFingerprint.user_id == user_id,
        AccessFingerprint.ip == ip,
        AccessFingerprint.device == device,
    ))
    now = utcnow()
    if fp is None:
        session.add(AccessFingerprint(
            user_id=user_id, ip=ip, device=device, first_seen=now, last_seen=now, hits=1
        ))
    else:
        fp.last_seen = now
        fp.hits = (fp.hits or 0) + 1


def _analyze_fps(fps: list) -> dict:
    ips = {f.ip for f in fps}
    devices = {f.device for f in fps}
    nets = {_net16(f.ip) for f in fps}
    # Одновременность: два разных «места» (сети) в пределах 5 минут — сильный сигнал.
    concurrent = None
    events = sorted(fps, key=lambda f: f.last_seen)
    for i in range(1, len(events)):
        a, b = events[i - 1], events[i]
        if _net16(a.ip) != _net16(b.ip) and (b.last_seen - a.last_seen).total_seconds() <= 300:
            concurrent = b.last_seen
            break
    score = 0
    if concurrent:
        score += 3
    if len(devices) >= 3:
        score += 2
    elif len(devices) == 2:
        score += 1
    if len(nets) >= 4:
        score += 2
    elif len(nets) == 3:
        score += 1
    level = "high" if score >= 3 else "medium" if score >= 2 else "low"
    return {
        "ips": len(ips), "devices": len(devices), "nets": len(nets),
        "concurrent": concurrent.isoformat() if concurrent else None,
        "score": score, "level": level,
    }


async def sharing_report(session: AsyncSession, days: int = 45) -> list[dict]:
    """Список подозрительных на шеринг аккаунтов (score ≥ 2), от опасных к менее."""
    since = utcnow() - timedelta(days=days)
    rows = list(await session.scalars(
        select(AccessFingerprint).where(AccessFingerprint.last_seen >= since)
    ))
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r.user_id, []).append(r)
    out = []
    for uid, fps in by_user.items():
        user = await session.get(User, uid)
        if user is None or settings.is_admin(user.telegram_id):
            continue
        a = _analyze_fps(fps)
        if a["score"] < 2:
            continue
        out.append({
            "telegram_id": user.telegram_id, "name": user.full_name,
            "has_access": user.has_access(), **a,
        })
    out.sort(key=lambda x: (-x["score"], -x["nets"], -x["devices"]))
    return out


async def user_access_detail(session: AsyncSession, user: User) -> dict:
    fps = list(await session.scalars(
        select(AccessFingerprint)
        .where(AccessFingerprint.user_id == user.id)
        .order_by(AccessFingerprint.last_seen.desc())
    ))
    a = _analyze_fps(fps) if fps else {
        "ips": 0, "devices": 0, "nets": 0, "concurrent": None, "score": 0, "level": "low"
    }
    return {
        "telegram_id": user.telegram_id, "name": user.full_name, "has_access": user.has_access(),
        **a,
        "fingerprints": [{
            "ip": f.ip,
            "device_label": device_label(f.device),
            "first_seen": f.first_seen.isoformat(),
            "last_seen": f.last_seen.isoformat(),
            "hits": f.hits or 1,
        } for f in fps[:80]],
    }


# --- Доступ ---

async def grant_access(session: AsyncSession, user: User, admin_id: int) -> dict | None:
    """Выдаёт доступ. Если ученик пришёл по реферальной ссылке и комиссия за него
    ещё не начислялась — начисляет её пригласившему и возвращает данные для
    уведомления {referrer_tg, reward, percent, earned_total}. Иначе None."""
    user.access_active = True
    user.access_granted_at = utcnow()
    if settings.ACCESS_DAYS > 0:
        user.access_expires_at = utcnow() + timedelta(days=settings.ACCESS_DAYS)
    else:
        user.access_expires_at = None
    session.add(
        AdminLog(admin_id=admin_id, action="grant_access", target_user_id=user.telegram_id)
    )
    return await _accrue_referral_reward(session, user)


# --- Реферальная программа ---

def _reward_amount(paid_count: int) -> tuple[int, int]:
    """Возвращает (сумма ₽, процент) за очередного оплатившего реферала."""
    percent = (
        settings.REFERRAL_PERCENT_FIRST if paid_count == 0
        else settings.REFERRAL_PERCENT_REST
    )
    return settings.COURSE_PRICE_RUB * percent // 100, percent


async def attach_referral(session: AsyncSession, user: User, referrer_tg: int) -> bool:
    """Закрепляет нового ученика за пригласившим (по telegram_id). Один раз,
    только пока у ученика нет доступа и он не привязан ранее, и не сам к себе."""
    if user.referred_by_id is not None or user.access_active:
        return False
    if referrer_tg == user.telegram_id:
        return False
    referrer = await get_user_by_tg(session, referrer_tg)
    if referrer is None:
        return False
    user.referred_by_id = referrer.id
    await session.flush()
    return True


async def _accrue_referral_reward(session: AsyncSession, user: User) -> dict | None:
    if bool(user.referral_rewarded) or user.referred_by_id is None:
        return None
    referrer = await session.get(User, user.referred_by_id)
    if referrer is None:
        return None
    paid_count = await session.scalar(
        select(func.count(User.id)).where(
            User.referred_by_id == referrer.id, User.referral_rewarded.is_(True)
        )
    ) or 0
    reward, percent = _reward_amount(paid_count)
    referrer.referral_earned = (referrer.referral_earned or 0) + reward
    user.referral_rewarded = True
    await session.flush()
    return {
        "referrer_tg": referrer.telegram_id,
        "reward": reward,
        "percent": percent,
        "earned_total": referrer.referral_earned,
    }


async def referral_stats(session: AsyncSession, user: User) -> dict:
    """Статистика пригласившего: сколько пришло, сколько оплатило, заработок."""
    came = await session.scalar(
        select(func.count(User.id)).where(User.referred_by_id == user.id)
    ) or 0
    paid = await session.scalar(
        select(func.count(User.id)).where(
            User.referred_by_id == user.id, User.referral_rewarded.is_(True)
        )
    ) or 0
    earned = user.referral_earned or 0
    paid_out = user.referral_paid_out or 0
    return {
        "came": came,
        "paid": paid,
        "earned": earned,
        "paid_out": paid_out,
        "available": max(0, earned - paid_out),
    }


async def mark_referral_paid(session: AsyncSession, user: User) -> int:
    """Отмечает всю доступную сумму как выплаченную. Возвращает выплаченное ₽."""
    available = max(0, (user.referral_earned or 0) - (user.referral_paid_out or 0))
    user.referral_paid_out = user.referral_earned or 0
    return available


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


async def approve_payment(
    session: AsyncSession, payment: Payment, admin_id: int
) -> tuple[User, dict | None]:
    payment.status = PaymentStatus.APPROVED
    payment.reviewed_at = utcnow()
    payment.reviewed_by = admin_id
    user = await session.get(User, payment.user_id)
    reward = await grant_access(session, user, admin_id)
    return user, reward


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


async def save_position(session: AsyncSession, user_id: int, material_id: int, position: int) -> None:
    """Сохраняет секунду, на которой ученик остановил видео."""
    progress = await get_progress(session, user_id, material_id)
    if progress is None:
        progress = MaterialProgress(user_id=user_id, material_id=material_id)
        session.add(progress)
    progress.position = max(0, int(position))
    progress.opened_at = utcnow()


# --- Геймификация: стрик, прогресс курса, достижения ---

async def touch_activity(session: AsyncSession, user: User) -> int:
    """Обновляет серию активных дней (стрик). Вызывать раз при заходе."""
    today = utcnow().date()
    last = user.last_active_date
    if last == today:
        return user.streak or 0
    if last == today - timedelta(days=1):
        user.streak = (user.streak or 0) + 1
    else:
        user.streak = 1
    user.last_active_date = today
    return user.streak


async def course_progress(session: AsyncSession, user: User) -> int:
    videos = await visible_materials(session, MaterialKind.VIDEO)
    total = len(videos)
    if not total:
        return 0
    watched = await watched_material_ids(session, user.id)
    done = len({m.id for m in videos} & watched)
    return round(done / total * 100)


async def badges(session: AsyncSession, user: User) -> list[dict]:
    summary = await progress_summary(session, user)
    wv, tp, streak = summary["videos_watched"], summary["tests_passed"], user.streak or 0
    attempts = list(await session.scalars(select(TestAttempt).where(TestAttempt.user_id == user.id)))
    any_100 = any(a.total > 0 and a.score == a.total for a in attempts)
    board = await leaderboard(session)
    rank = next((r["rank"] for r in board if r["user_id"] == user.id), None)

    defs = [
        ("first_video", "🎬", "Первый вебинар", "Посмотрите 1 вебинар", wv >= 1),
        ("marathon", "🍿", "Марафонец", "Посмотрите 5 вебинаров", wv >= 5),
        ("first_test", "📝", "Первый тест", "Пройдите 1 тест", tp >= 1),
        ("excellent", "⭐", "Отличник", "Сдайте тест на 100%", any_100),
        ("streak3", "🔥", "В ударе", "Заходите 3 дня подряд", streak >= 3),
        ("streak7", "💎", "Неделя подряд", "Заходите 7 дней подряд", streak >= 7),
        ("top3", "🏆", "Призёр", "Попадите в топ-3 рейтинга", rank is not None and rank <= 3),
    ]
    return [{"id": i, "icon": ic, "title": t, "desc": d, "earned": bool(e)} for (i, ic, t, d, e) in defs]


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


# --- Рейтинг активных ---

POINTS_PER_VIDEO = 10   # за каждый просмотренный вебинар
POINTS_PER_TEST_CORRECT = 5  # за каждый верный ответ (по лучшей попытке теста)


async def leaderboard(session: AsyncSession) -> list[dict]:
    """Считает баллы всех учеников: просмотры вебинаров + лучшие результаты тестов."""
    users = list(await session.scalars(select(User)))

    watched_rows = await session.scalars(
        select(MaterialProgress.user_id).where(MaterialProgress.watched.is_(True))
    )
    watched_by_user: dict[int, int] = {}
    for uid in watched_rows:
        watched_by_user[uid] = watched_by_user.get(uid, 0) + 1

    attempts = list(await session.scalars(select(TestAttempt)))
    best: dict[tuple[int, int], int] = {}
    tests_by_user: dict[int, set[int]] = {}
    for a in attempts:
        key = (a.user_id, a.test_id)
        if a.score > best.get(key, -1):
            best[key] = a.score
        tests_by_user.setdefault(a.user_id, set()).add(a.test_id)
    test_points: dict[int, int] = {}
    for (uid, _tid), score in best.items():
        test_points[uid] = test_points.get(uid, 0) + score

    rows = []
    for u in users:
        wv = watched_by_user.get(u.id, 0)
        tp = test_points.get(u.id, 0)
        points = wv * POINTS_PER_VIDEO + tp * POINTS_PER_TEST_CORRECT
        if points > 0:
            rows.append({
                "user_id": u.id, "name": u.full_name, "points": points,
                "videos": wv, "tests": len(tests_by_user.get(u.id, set())),
            })
    rows.sort(key=lambda r: (-r["points"], r["name"].lower()))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


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
