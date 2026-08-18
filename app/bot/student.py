"""Хендлеры бота для ученика: современное инлайн-меню с навигацией."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from ..config import settings
from ..db import session_scope
from .. import services
from .instance import get_bot
from . import keyboards as kb
from . import texts
from .notify import notify_admins, notify_admins_photo

router = Router(name="student")

_bot_username_cache: str | None = None


def _parse_ref_payload(text: str | None) -> int | None:
    """Из «/start ref_12345» достаёт telegram_id пригласившего."""
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    if payload.startswith("ref_"):
        rest = payload[4:]
        if rest.isdigit():
            return int(rest)
    return None


async def _bot_username() -> str:
    global _bot_username_cache
    if settings.BOT_USERNAME:
        return settings.BOT_USERNAME
    if _bot_username_cache:
        return _bot_username_cache
    bot = get_bot()
    try:
        me = await bot.get_me()
        _bot_username_cache = me.username or "finyrobot"
    except Exception:
        _bot_username_cache = "finyrobot"
    return _bot_username_cache


async def _referral_link(telegram_id: int) -> str:
    return f"https://t.me/{await _bot_username()}?start=ref_{telegram_id}"


async def _show_referral(target, telegram_id: int, edit: bool) -> None:
    """Показывает экран реферальной программы (target — Message или CallbackQuery)."""
    async with session_scope() as session:
        user = await services.get_or_create_user(session, telegram_id)
        stats = await services.referral_stats(session, user)
        await session.commit()
    link = await _referral_link(telegram_id)
    text = texts.referral_text(link, stats)
    share_text = "Курс по финансовой грамотности «Финуро» — вебинары, теория и тесты 👇"
    markup = kb.referral_inline(link, share_text, stats["available"])
    if edit:
        await _edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


class PayFlow(StatesGroup):
    waiting_proof = State()


class SupportFlow(StatesGroup):
    waiting_message = State()


# ─────────────────────────── Хелперы ──────────────────────────

async def _touch_user(tg_user) -> tuple[str, bool]:
    """Регистрирует/обновляет пользователя, возвращает (имя, есть_доступ)."""
    async with session_scope() as session:
        user = await services.get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name
        )
        await session.commit()
        return user.full_name, user.has_access()


async def _edit(call: CallbackQuery, text: str, markup=None) -> None:
    """Редактирует текущее сообщение (навигация внутри одного сообщения)."""
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)
    try:
        await call.answer()
    except Exception:
        pass


async def _send_menu(message: Message) -> None:
    name, has = await _touch_user(message.from_user)
    await message.answer(texts.main_menu_text(name, has), reply_markup=kb.main_menu_inline(has))


# ─────────────────────────── Команды ──────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    tg = message.from_user
    ref_tg = _parse_ref_payload(message.text)
    async with session_scope() as session:
        existing = await services.get_user_by_tg(session, tg.id)
        is_new = existing is None
        user = await services.get_or_create_user(
            session, tg.id, tg.username, tg.first_name, tg.last_name
        )
        # Закрепляем за пригласившим только новых пользователей, пришедших по ссылке.
        if is_new and ref_tg is not None:
            await services.attach_referral(session, user, ref_tg)
        await session.commit()
        name, has = user.full_name, user.has_access()
    # Первое сообщение убирает старую reply-клавиатуру (если осталась от прошлых версий).
    await message.answer("🎓 Добро пожаловать в <b>Финуро</b>!", reply_markup=ReplyKeyboardRemove())
    await message.answer(texts.main_menu_text(name, has), reply_markup=kb.main_menu_inline(has))


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_menu(message)


@router.message(Command("ref"))
async def cmd_ref(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_referral(message, message.from_user.id, edit=False)


@router.callback_query(F.data == "menu:ref")
async def cb_ref(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_referral(call, call.from_user.id, edit=True)


@router.callback_query(F.data == "ref:payout")
async def cb_ref_payout(call: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, call.from_user.id)
        stats = await services.referral_stats(session, user)
        await session.commit()
        full_name, tg = user.full_name, user.telegram_id
    available = stats["available"]
    if available <= 0:
        await call.answer("Пока нечего выплачивать", show_alert=True)
        return
    await notify_admins(
        "💸 <b>Запрос на выплату по реферальной программе</b>\n"
        f"От: {full_name} (<code>{tg}</code>)\n"
        f"К выплате: <b>{available} ₽</b>\n"
        f"Приглашено оплативших: {stats['paid']} · заработано всего: {stats['earned']} ₽",
        reply_markup=kb.referral_payout_admin(tg),
    )
    await _edit(
        call,
        f"✅ Запрос на выплату <b>{available} ₽</b> отправлен администратору.\n"
        "Мы свяжемся с вами для перечисления средств.",
        kb.back_inline(),
    )


# ─────────────────────────── Навигация (callbacks) ────────────

@router.callback_query(F.data == "menu:main")
async def cb_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    name, has = await _touch_user(call.from_user)
    await _edit(call, texts.main_menu_text(name, has), kb.main_menu_inline(has))


@router.callback_query(F.data == "menu:about")
async def cb_about(call: CallbackQuery) -> None:
    await _edit(call, texts.about_text(), kb.back_inline())


@router.callback_query(F.data == "menu:how")
async def cb_how(call: CallbackQuery) -> None:
    await _edit(call, texts.how_text(), kb.back_inline())


@router.callback_query(F.data == "menu:status")
async def cb_status(call: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, call.from_user.id)
        payment = await services.latest_payment(session, user)
        await session.commit()
        text = texts.status_text(user, payment)
    await _edit(call, text, kb.back_inline())


@router.callback_query(F.data == "menu:pay")
async def cb_pay(call: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, call.from_user.id)
        has = user.has_access()
    if has:
        await _edit(call, "У вас уже есть доступ 🎉 Нажмите «В меню» и откройте приложение.", kb.back_inline())
        return
    await _edit(call, texts.payment_requisites(), kb.payment_inline())


@router.callback_query(F.data == "pay:send")
async def cb_pay_send(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PayFlow.waiting_proof)
    await _edit(
        call,
        "📸 Пришлите <b>скриншот или файл</b> чека об оплате одним сообщением.",
        kb.cancel_inline(),
    )


@router.callback_query(F.data == "menu:support")
async def cb_support(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportFlow.waiting_message)
    await _edit(call, texts.SUPPORT_PROMPT, kb.cancel_inline())


@router.callback_query(F.data == "nav:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    name, has = await _touch_user(call.from_user)
    await _edit(call, texts.main_menu_text(name, has), kb.main_menu_inline(has))


# ─────────────────────────── Приём чека оплаты ────────────────

@router.message(PayFlow.waiting_proof, F.photo)
@router.message(PayFlow.waiting_proof, F.document)
async def receive_proof(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    async with session_scope() as session:
        user = await services.get_or_create_user(
            session, message.from_user.id, message.from_user.username,
            message.from_user.first_name, message.from_user.last_name,
        )
        payment = await services.create_payment(session, user, file_id)
        await session.commit()
        payment_id, full_name, tg = payment.id, user.full_name, user.telegram_id
    await state.clear()

    caption = (
        f"🧾 <b>Новая заявка на оплату</b> #{payment_id}\n"
        f"Ученик: {full_name}\n"
        f"ID: <code>{tg}</code>\n"
        f"Сумма: {settings.COURSE_PRICE}"
    )
    await notify_admins_photo(file_id, caption, reply_markup=kb.payment_review(payment_id))
    await message.answer(texts.PROOF_RECEIVED)
    await _send_menu(message)


@router.message(PayFlow.waiting_proof)
async def proof_not_a_file(message: Message) -> None:
    await message.answer("Пришлите, пожалуйста, скриншот или файл чека 📸 (или нажмите «Отмена»).")


# ─────────────────────────── Поддержка ────────────────────────

@router.message(SupportFlow.waiting_message, F.text)
async def receive_support(message: Message, state: FSMContext) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(
            session, message.from_user.id, message.from_user.username,
            message.from_user.first_name, message.from_user.last_name,
        )
        await services.add_support_message(session, user, message.text, from_admin=False)
        await session.commit()
        full_name, tg = user.full_name, user.telegram_id
    await state.clear()

    await notify_admins(
        f"✉️ <b>Обращение в поддержку</b>\n"
        f"От: {full_name} (<code>{tg}</code>)\n\n"
        f"{message.text}\n\n"
        f"<i>Ответьте на это сообщение (reply), чтобы написать ученику.</i>\n"
        f"#u{tg}"
    )
    await message.answer(texts.SUPPORT_SENT)
    await _send_menu(message)


# ─────────────────────────── Фолбэк ───────────────────────────

@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    # Любое сообщение вне сценария — показываем меню.
    await _send_menu(message)
