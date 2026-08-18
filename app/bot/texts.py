"""Тексты сообщений бота (HTML-разметка)."""
from __future__ import annotations

from html import escape

from ..config import settings


def main_menu_text(name: str, has_access: bool) -> str:
    status = "🟢 <b>Доступ активен</b>" if has_access else "🔴 Доступ не активирован"
    lines = [
        f"👋 Привет, <b>{escape(name or 'друг')}</b>!",
        "",
        "Это бот курса <b>Финуро</b> — подготовка к отборочным этапам по финансовой грамотности.",
        "",
        status,
    ]
    if not has_access:
        lines.append(f"💰 Стоимость доступа: <b>{escape(settings.COURSE_PRICE)}</b>")
    lines.append("\nВыберите, что интересует 👇")
    return "\n".join(lines)


def about_text() -> str:
    return (
        "🎓 <b>О курсе «Финуро»</b>\n\n"
        f"{escape(settings.COURSE_DESCRIPTION)}\n\n"
        "<b>Что внутри приложения:</b>\n"
        "• 🎬 Вебинары — разбор тем и заданий\n"
        "• 📚 Теория — структурированные материалы\n"
        "• 📝 Тесты — автопроверка и прогресс\n"
        "• 📅 Расписание — вебинары, дедлайны, олимпиады\n\n"
        "Материалы открываются постепенно — вы идёте по программе без перегруза.\n\n"
        f"💰 Стоимость доступа: <b>{escape(settings.COURSE_PRICE)}</b>"
    )


def how_text() -> str:
    return (
        "❓ <b>Как проходит обучение</b>\n\n"
        "1️⃣ Оплачиваете доступ и присылаете чек\n"
        "2️⃣ Мы подтверждаем оплату — доступ открывается\n"
        "3️⃣ Открываете приложение прямо в Telegram\n"
        "4️⃣ Смотрите вебинары, проходите тесты и изучаете теорию по мере публикации\n\n"
        "Появятся вопросы — раздел «💬 Поддержка», ответим здесь же."
    )


def welcome() -> str:
    return (
        f"<b>Финуро</b> — {settings.COURSE_TITLE}\n\n"
        f"{settings.COURSE_DESCRIPTION}\n\n"
        f"💰 Стоимость доступа: <b>{settings.COURSE_PRICE}</b>\n\n"
        "Материалы (вебинары, теория, тесты) открываются постепенно в личном "
        "приложении. Нажмите «💳 Оплатить доступ», чтобы начать."
    )


def payment_requisites() -> str:
    return (
        "💳 <b>Оплата доступа</b>\n\n"
        f"{settings.PAY_REQUISITES}\n\n"
        "⚠️ Доступ персональный. Передача материалов третьим лицам — основание "
        "для блокировки без возврата средств."
    )


PROOF_RECEIVED = (
    "✅ Заявка принята и передана администратору на проверку.\n"
    "Как только оплата подтвердится, я пришлю кнопку для входа в приложение."
)

ACCESS_GRANTED = (
    "🎉 <b>Доступ открыт!</b>\n\n"
    "Теперь вам доступно личное приложение с вебинарами, теорией и тестами. "
    "Нажмите кнопку ниже, чтобы войти."
)

ACCESS_REVOKED = (
    "🔒 Ваш доступ к материалам приостановлен. "
    "Если это ошибка — напишите в поддержку."
)

PAYMENT_REJECTED = (
    "❌ К сожалению, оплата не подтверждена.\n"
    "Проверьте реквизиты и попробуйте снова через «💳 Оплатить доступ», "
    "либо напишите в поддержку."
)

SUPPORT_PROMPT = (
    "✍️ Напишите сообщение — я передам его администратору. "
    "Ответ придёт сюда же, в этот чат."
)

SUPPORT_SENT = "✅ Сообщение отправлено администратору. Ожидайте ответа."

NEED_ACCESS = (
    "🔒 Приложение доступно после подтверждения оплаты. "
    "Нажмите «💳 Оплатить доступ»."
)


def referral_text(link: str, stats: dict) -> str:
    first = settings.REFERRAL_PERCENT_FIRST
    rest = settings.REFERRAL_PERCENT_REST
    return (
        "🎁 <b>Реферальная программа Финуро</b>\n\n"
        "Приглашайте друзей по своей персональной ссылке и получайте вознаграждение "
        "за каждого, кто оплатит курс:\n"
        f"• за 1-го оплатившего — <b>{first}%</b>\n"
        f"• за каждого следующего — <b>{rest}%</b>\n\n"
        "🔗 <b>Ваша ссылка</b> (нажмите, чтобы скопировать):\n"
        f"<code>{escape(link)}</code>\n\n"
        "📊 <b>Ваша статистика</b>\n"
        f"• Перешли по ссылке: <b>{stats['came']}</b>\n"
        f"• Оплатили курс: <b>{stats['paid']}</b>\n"
        f"• Заработано всего: <b>{stats['earned']} ₽</b>\n"
        f"• Выплачено: <b>{stats['paid_out']} ₽</b>\n"
        f"• Доступно к выплате: <b>{stats['available']} ₽</b>"
    )


def referral_earned_notice(reward: int, percent: int, earned_total: int) -> str:
    return (
        "🎉 <b>Реферальное вознаграждение!</b>\n\n"
        f"Приглашённый вами человек оплатил курс. Вам начислено "
        f"<b>{reward} ₽</b> ({percent}%).\n"
        f"Всего заработано: <b>{earned_total} ₽</b>.\n\n"
        "Открыть программу и запросить выплату — команда /ref."
    )


def referral_payout_done(amount: int) -> str:
    return (
        f"✅ Выплата <b>{amount} ₽</b> отправлена!\n"
        "Спасибо, что рекомендуете Финуро 💜"
    )


def status_text(user, payment) -> str:
    if user.has_access():
        line = "🟢 Доступ активен"
        if user.access_expires_at:
            line += f" (до {user.access_expires_at:%d.%m.%Y})"
    elif payment and payment.status == "pending":
        line = "🟡 Заявка на оплату на проверке"
    else:
        line = "🔴 Доступ не активен"
    return f"<b>Ваш статус</b>\n\n{line}"
