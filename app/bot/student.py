"""Хендлеры бота для ученика: старт, оплата, статус, поддержка."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..config import settings
from ..db import session_scope
from .. import services
from . import keyboards as kb
from . import texts
from .notify import notify_admins, notify_admins_photo

router = Router(name="student")


class PayFlow(StatesGroup):
    waiting_proof = State()


class SupportFlow(StatesGroup):
    waiting_message = State()


async def _menu_for(telegram_id: int) -> kb.ReplyKeyboardMarkup:
    async with session_scope() as session:
        user = await services.get_user_by_tg(session, telegram_id)
        return kb.student_menu(bool(user and user.has_access()))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    async with session_scope() as session:
        user = await services.get_or_create_user(
            session, u.id, u.username, u.first_name, u.last_name
        )
        await session.commit()
        has_access = user.has_access()
    await message.answer(texts.welcome(), reply_markup=kb.student_menu(has_access))


@router.message(Command("status"))
@router.message(F.text == kb.BTN_STATUS)
async def show_status(message: Message) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, message.from_user.id)
        payment = await services.latest_payment(session, user)
        await session.commit()
        text = texts.status_text(user, payment)
        menu = kb.student_menu(user.has_access())
    await message.answer(text, reply_markup=menu)


# --- Оплата ---

@router.message(F.text == kb.BTN_PAY)
async def start_payment(message: Message, state: FSMContext) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, message.from_user.id)
        if user.has_access():
            await message.answer("У вас уже есть доступ 🎉", reply_markup=kb.student_menu(True))
            return
    await state.set_state(PayFlow.waiting_proof)
    await message.answer(texts.payment_requisites(), reply_markup=kb.cancel_menu())


@router.message(PayFlow.waiting_proof, F.text == kb.BTN_CANCEL)
@router.message(SupportFlow.waiting_message, F.text == kb.BTN_CANCEL)
async def cancel_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=await _menu_for(message.from_user.id))


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
        payment_id, full_name = payment.id, user.full_name
        tg = user.telegram_id
    await state.clear()

    caption = (
        f"🧾 <b>Новая заявка на оплату</b> #{payment_id}\n"
        f"Ученик: {full_name}\n"
        f"ID: <code>{tg}</code>\n"
        f"Сумма: {settings.COURSE_PRICE}"
    )
    await notify_admins_photo(file_id, caption, reply_markup=kb.payment_review(payment_id))
    await message.answer(texts.PROOF_RECEIVED, reply_markup=await _menu_for(message.from_user.id))


@router.message(PayFlow.waiting_proof)
async def proof_not_a_file(message: Message) -> None:
    await message.answer(
        "Пришлите, пожалуйста, скриншот или файл чека об оплате. "
        "Или нажмите «◀️ Отмена»."
    )


# --- Поддержка ---

@router.message(F.text == kb.BTN_SUPPORT)
async def start_support(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportFlow.waiting_message)
    await message.answer(texts.SUPPORT_PROMPT, reply_markup=kb.cancel_menu())


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
    await message.answer(texts.SUPPORT_SENT, reply_markup=await _menu_for(message.from_user.id))


# --- Фолбэк для обычных сообщений ---

@router.message(StateFilter(None), F.text == kb.BTN_OPEN_APP)
async def open_app_hint(message: Message) -> None:
    # Обычно web_app открывается кнопкой; сюда попадаем, если URL не настроен.
    if not settings.MINIAPP_URL.startswith("https://"):
        await message.answer("Приложение ещё не подключено администратором.")


@router.message(StateFilter(None), F.text)
async def fallback(message: Message) -> None:
    async with session_scope() as session:
        user = await services.get_or_create_user(session, message.from_user.id)
        await session.commit()
        has_access = user.has_access()
    hint = "Выберите действие в меню ниже 👇"
    if not has_access:
        hint = texts.NEED_ACCESS
    await message.answer(hint, reply_markup=kb.student_menu(has_access))
