"""Админ-панель внутри бота. Доступна только Telegram ID из ADMIN_IDS."""
from __future__ import annotations

import re
from html import escape

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, func, select

from ..config import settings
from ..db import session_scope
from .. import services
from ..models import (
    Material,
    MaterialKind,
    MaterialStatus,
    Test,
    TestAttempt,
    User,
)
from . import keyboards as kb
from . import texts
from .notify import send_to_user

router = Router(name="admin")


async def _notify_referrer(reward: dict | None) -> None:
    """Сообщает пригласившему о начисленной комиссии (если она была)."""
    if not reward:
        return
    await send_to_user(
        reward["referrer_tg"],
        texts.referral_earned_notice(reward["reward"], reward["percent"], reward["earned_total"]),
    )


class AdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and settings.is_admin(event.from_user.id))


router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

_USER_MARKER = re.compile(r"#u(\d+)")


class AddVideo(StatesGroup):
    title = State()
    stream_url = State()
    description = State()


class AddTheory(StatesGroup):
    title = State()
    content = State()


class Broadcast(StatesGroup):
    text = State()


# --- Меню ---

@router.message(Command("admin"))
async def admin_home(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        c = await services.counts(session)
    await message.answer(
        "🛠 <b>Админ-панель Финуро</b>\n\n"
        f"👥 Учеников: {c['total_users']} (активных: {c['active_users']})\n"
        f"📥 Заявок на проверке: {c['pending']}",
        reply_markup=kb.admin_menu(),
    )


# --- Заявки на оплату ---

@router.message(F.text == kb.BTN_A_PAYMENTS)
async def list_payments(message: Message) -> None:
    async with session_scope() as session:
        payments = await services.pending_payments(session)
        rows = []
        for p in payments:
            user = await session.get(User, p.user_id)
            rows.append((p, user))
    if not rows:
        await message.answer("Заявок на проверке нет ✅")
        return
    await message.answer(f"📥 Заявок на проверке: {len(rows)}")
    for p, user in rows:
        caption = (
            f"🧾 Заявка #{p.id}\n"
            f"Ученик: {escape(user.full_name)}\n"
            f"ID: <code>{user.telegram_id}</code>\n"
            f"Создана: {p.created_at:%d.%m.%Y %H:%M}"
        )
        markup = kb.payment_review(p.id)
        from .instance import get_bot
        bot = get_bot()
        sent = False
        if p.proof_file_id and bot:
            # Чек мог быть фото ИЛИ файлом — пробуем оба способа, затем текст.
            for send in (bot.send_photo, bot.send_document):
                try:
                    await send(message.chat.id, p.proof_file_id, caption=caption, reply_markup=markup)
                    sent = True
                    break
                except Exception:
                    continue
        if not sent:
            await message.answer(caption, reply_markup=markup)


@router.callback_query(F.data.startswith("pay:"))
async def review_payment(call: CallbackQuery) -> None:
    _, action, pid = call.data.split(":")
    async with session_scope() as session:
        payment = await services.get_payment(session, int(pid))
        if payment is None:
            await call.answer("Заявка не найдена", show_alert=True)
            return
        if payment.status != "pending":
            await call.answer("Заявка уже обработана", show_alert=True)
            return
        if action == "approve":
            user, reward = await services.approve_payment(session, payment, call.from_user.id)
            await session.commit()
            await send_to_user(user.telegram_id, texts.ACCESS_GRANTED, reply_markup=kb.open_app_inline())
            await send_to_user(user.telegram_id, texts.main_menu_text(user.full_name, True), reply_markup=kb.main_menu_inline(True))
            await _notify_referrer(reward)
            result = f"✅ Оплата подтверждена, доступ выдан ({user.full_name})"
        else:
            user = await services.reject_payment(session, payment, call.from_user.id)
            await session.commit()
            await send_to_user(user.telegram_id, texts.PAYMENT_REJECTED)
            result = f"❌ Заявка отклонена ({user.full_name})"
    await call.answer("Готово")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(result)


# --- Ученики ---

@router.message(F.text == kb.BTN_A_USERS)
async def list_users(message: Message) -> None:
    async with session_scope() as session:
        users = list(
            await session.scalars(select(User).order_by(desc(User.created_at)).limit(15))
        )
    if not users:
        await message.answer("Учеников пока нет.")
        return
    await message.answer(f"👥 Последние ученики ({len(users)}):")
    for u in users:
        status = "🟢 активен" if u.has_access() else "🔴 нет доступа"
        await message.answer(
            f"{u.full_name}\nID: <code>{u.telegram_id}</code>\nСтатус: {status}",
            reply_markup=kb.user_manage(u.telegram_id, u.has_access()),
        )


@router.callback_query(F.data.startswith("refpaid:"))
async def mark_referral_paid(call: CallbackQuery) -> None:
    tg = int(call.data.split(":")[1])
    async with session_scope() as session:
        user = await services.get_user_by_tg(session, tg)
        if user is None:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        amount = await services.mark_referral_paid(session, user)
        await session.commit()
        full_name = user.full_name
    if amount <= 0:
        await call.answer("Нет доступной суммы к выплате", show_alert=True)
        return
    await send_to_user(tg, texts.referral_payout_done(amount))
    await call.answer("Отмечено выплаченным")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"💸 Отмечено: выплачено {amount} ₽ ({full_name})")


@router.callback_query(F.data.startswith("user:"))
async def manage_user(call: CallbackQuery) -> None:
    _, action, tg = call.data.split(":")
    async with session_scope() as session:
        user = await services.get_user_by_tg(session, int(tg))
        if user is None:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        if action == "grant":
            reward = await services.grant_access(session, user, call.from_user.id)
            await session.commit()
            await send_to_user(user.telegram_id, texts.ACCESS_GRANTED, reply_markup=kb.open_app_inline())
            await send_to_user(user.telegram_id, texts.main_menu_text(user.full_name, True), reply_markup=kb.main_menu_inline(True))
            await _notify_referrer(reward)
            note = "🔓 Доступ выдан"
        else:
            await services.revoke_access(session, user, call.from_user.id)
            await session.commit()
            await send_to_user(user.telegram_id, texts.ACCESS_REVOKED, reply_markup=kb.main_menu_inline(False))
            note = "🔒 Доступ отозван"
        has_access = user.has_access()
    await call.answer(note)
    try:
        await call.message.edit_reply_markup(reply_markup=kb.user_manage(int(tg), has_access))
    except Exception:
        pass


# --- Контент ---

@router.message(F.text == kb.BTN_A_CONTENT)
async def content_home(message: Message) -> None:
    await message.answer("🎬 <b>Управление контентом</b>", reply_markup=kb.content_menu())


async def _next_order(session, kind: str) -> int:
    m = await session.scalar(
        select(func.max(Material.order_index)).where(Material.kind == kind)
    )
    return (m or 0) + 1


@router.callback_query(F.data == "content:add_video")
async def add_video_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddVideo.title)
    await call.message.answer("Введите название вебинара:", reply_markup=kb.cancel_menu())
    await call.answer()


@router.message(AddVideo.title, F.text)
async def add_video_title(message: Message, state: FSMContext) -> None:
    if message.text == kb.BTN_CANCEL:
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.admin_menu())
        return
    await state.update_data(title=message.text)
    await state.set_state(AddVideo.stream_url)
    await message.answer(
        "Пришлите ссылку на видео (HLS .m3u8 или mp4). "
        "Она будет отдаваться только внутри приложения."
    )


@router.message(AddVideo.stream_url, F.text)
async def add_video_url(message: Message, state: FSMContext) -> None:
    await state.update_data(stream_url=message.text.strip())
    await state.set_state(AddVideo.description)
    await message.answer("Короткое описание вебинара (или «-», чтобы пропустить):")


@router.message(AddVideo.description, F.text)
async def add_video_desc(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    desc_text = None if message.text.strip() == "-" else message.text.strip()
    async with session_scope() as session:
        order = await _next_order(session, MaterialKind.VIDEO)
        mat = Material(
            kind=MaterialKind.VIDEO,
            title=data["title"],
            stream_url=data["stream_url"],
            description=desc_text,
            status=MaterialStatus.DRAFT,
            order_index=order,
        )
        session.add(mat)
        await session.commit()
        mat_id = mat.id
    await state.clear()
    await message.answer(
        f"✅ Вебинар создан как черновик (#{mat_id}). Опубликуйте, когда будете готовы:",
        reply_markup=kb.admin_menu(),
    )
    await message.answer(
        f"🎬 {data['title']}", reply_markup=kb.material_toggle(mat_id, published=False)
    )


@router.callback_query(F.data == "content:add_theory")
async def add_theory_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTheory.title)
    await call.message.answer("Введите название темы теории:", reply_markup=kb.cancel_menu())
    await call.answer()


@router.message(AddTheory.title, F.text)
async def add_theory_title(message: Message, state: FSMContext) -> None:
    if message.text == kb.BTN_CANCEL:
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.admin_menu())
        return
    await state.update_data(title=message.text)
    await state.set_state(AddTheory.content)
    await message.answer("Пришлите текст темы (можно длинным сообщением):")


@router.message(AddTheory.content, F.text)
async def add_theory_content(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with session_scope() as session:
        order = await _next_order(session, MaterialKind.THEORY)
        mat = Material(
            kind=MaterialKind.THEORY,
            title=data["title"],
            content=message.text,
            status=MaterialStatus.DRAFT,
            order_index=order,
        )
        session.add(mat)
        await session.commit()
        mat_id = mat.id
    await state.clear()
    await message.answer(
        f"✅ Тема создана как черновик (#{mat_id}).", reply_markup=kb.admin_menu()
    )
    await message.answer(
        f"📚 {data['title']}", reply_markup=kb.material_toggle(mat_id, published=False)
    )


@router.callback_query(F.data == "content:list")
async def content_list(call: CallbackQuery) -> None:
    async with session_scope() as session:
        materials = await services.all_materials(session)
    await call.answer()
    if not materials:
        await call.message.answer("Материалов пока нет.")
        return
    for m in materials:
        icon = "🎬" if m.kind == MaterialKind.VIDEO else "📚"
        published = m.status == MaterialStatus.PUBLISHED
        state_label = "🟢 опубликован" if published else f"⚪️ {m.status}"
        await call.message.answer(
            f"{icon} #{m.id} {m.title}\n{state_label}",
            reply_markup=kb.material_toggle(m.id, published),
        )


@router.callback_query(F.data.startswith("mat:"))
async def toggle_material(call: CallbackQuery) -> None:
    _, action, mid = call.data.split(":")
    async with session_scope() as session:
        mat = await session.get(Material, int(mid))
        if mat is None:
            await call.answer("Материал не найден", show_alert=True)
            return
        if action == "pub":
            mat.status = MaterialStatus.PUBLISHED
            note = "Опубликовано ✅"
        else:
            mat.status = MaterialStatus.DRAFT
            note = "Снято с публикации"
        await session.commit()
        published = mat.status == MaterialStatus.PUBLISHED
    await call.answer(note)
    try:
        await call.message.edit_reply_markup(reply_markup=kb.material_toggle(int(mid), published))
    except Exception:
        pass


# --- Результаты тестов ---

@router.message(F.text == kb.BTN_A_RESULTS)
async def results(message: Message) -> None:
    async with session_scope() as session:
        attempts = list(
            await session.scalars(
                select(TestAttempt).order_by(desc(TestAttempt.created_at)).limit(20)
            )
        )
        if not attempts:
            await message.answer("Результатов пока нет.")
            return
        lines = ["📊 <b>Последние результаты тестов</b>\n"]
        for a in attempts:
            user = await session.get(User, a.user_id)
            test = await session.get(Test, a.test_id)
            name = user.full_name if user else f"id{a.user_id}"
            title = test.title if test else f"тест #{a.test_id}"
            pct = round(a.score / a.total * 100) if a.total else 0
            lines.append(f"• {name} — «{title}»: {a.score}/{a.total} ({pct}%)")
    await message.answer("\n".join(lines))


# --- Обращения / поддержка ---

@router.message(F.text == kb.BTN_A_SUPPORT)
async def support_list(message: Message) -> None:
    from ..models import SupportMessage
    async with session_scope() as session:
        msgs = list(
            await session.scalars(
                select(SupportMessage)
                .where(SupportMessage.from_admin.is_(False))
                .order_by(desc(SupportMessage.created_at))
                .limit(10)
            )
        )
        if not msgs:
            await message.answer("Обращений пока нет.")
            return
        lines = ["✉️ <b>Последние обращения</b>\n"]
        for m in msgs:
            user = await session.get(User, m.user_id)
            name = user.full_name if user else f"id{m.user_id}"
            tg = user.telegram_id if user else "?"
            lines.append(
                f"• {name} (<code>{tg}</code>) — {m.created_at:%d.%m %H:%M}:\n{m.text}"
            )
        lines.append("\nОтветить: <code>/reply ID текст</code> "
                     "или reply на уведомление о заявке.")
    await message.answer("\n".join(lines))


@router.message(Command("reply"))
async def cmd_reply(message: Message) -> None:
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Формат: <code>/reply ID текст ответа</code>")
        return
    target_tg, text = int(parts[1]), parts[2]
    await _reply_to_user(message, target_tg, text)


@router.message(StateFilter(None), F.reply_to_message)
async def reply_via_reply(message: Message) -> None:
    """Ответ ученику через reply на уведомление, содержащее маркер #u<id>."""
    src = message.reply_to_message.text or message.reply_to_message.caption or ""
    match = _USER_MARKER.search(src)
    if not match or not message.text:
        return
    await _reply_to_user(message, int(match.group(1)), message.text)


async def _reply_to_user(message: Message, target_tg: int, text: str) -> None:
    async with session_scope() as session:
        user = await services.get_user_by_tg(session, target_tg)
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        await services.add_support_message(session, user, text, from_admin=True)
        await services.log_admin(session, message.from_user.id, "support_reply", target_tg)
        await session.commit()
    ok = await send_to_user(target_tg, f"💬 <b>Ответ поддержки:</b>\n\n{text}")
    await message.answer("Отправлено ✅" if ok else "Не удалось отправить (пользователь не писал боту?).")


# --- Рассылка ---

@router.message(F.text == kb.BTN_A_BROADCAST)
async def broadcast_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Broadcast.text)
    await message.answer(
        "Введите текст рассылки для всех учеников с активным доступом:",
        reply_markup=kb.cancel_menu(),
    )


@router.message(Broadcast.text, F.text)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if message.text == kb.BTN_CANCEL:
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.admin_menu())
        return
    async with session_scope() as session:
        ids = await services.active_user_tg_ids(session)
        await services.log_admin(session, message.from_user.id, "broadcast", details=message.text[:200])
        await session.commit()
    await state.clear()
    sent = 0
    for tg in ids:
        if await send_to_user(tg, f"📢 {message.text}"):
            sent += 1
    await message.answer(f"Рассылка завершена: доставлено {sent} из {len(ids)}.", reply_markup=kb.admin_menu())
