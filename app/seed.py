"""Демо-наполнение БД при первом запуске (если материалов ещё нет).

Помогает сразу увидеть, как работает мини-апп. В продакшене можно очистить
таблицы и наполнить своим контентом через админ-панель бота.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from .db import session_scope
from .models import Material, MaterialKind, MaterialStatus, Question, Test

log = logging.getLogger("finyro.seed")

# Публичный тестовый HLS-поток (Apple sample) — просто чтобы плеер ожил.
_DEMO_HLS = "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8"


async def seed_if_empty() -> None:
    async with session_scope() as session:
        count = await session.scalar(select(func.count(Material.id)))
        if count and count > 0:
            return
        log.info("База пуста — добавляю демо-контент.")

        session.add_all(
            [
                Material(
                    kind=MaterialKind.VIDEO,
                    title="Вводный вебинар: как устроен отборочный этап",
                    description="Формат заданий, критерии и стратегия подготовки.",
                    stream_url=_DEMO_HLS,
                    status=MaterialStatus.PUBLISHED,
                    order_index=1,
                ),
                Material(
                    kind=MaterialKind.VIDEO,
                    title="Личные финансы и бюджет",
                    description="Доходы, расходы, подушка безопасности.",
                    stream_url=_DEMO_HLS,
                    status=MaterialStatus.PUBLISHED,
                    order_index=2,
                ),
                Material(
                    kind=MaterialKind.VIDEO,
                    title="Инвестиции: базовые понятия (скоро)",
                    description="Откроется на следующей неделе.",
                    stream_url=_DEMO_HLS,
                    status=MaterialStatus.DRAFT,
                    order_index=3,
                ),
                Material(
                    kind=MaterialKind.THEORY,
                    title="Тема 1. Что такое финансовая грамотность",
                    content=(
                        "Финансовая грамотность — это набор знаний и навыков, которые "
                        "помогают принимать взвешенные решения о деньгах: планировать "
                        "бюджет, копить, защищаться от рисков и понимать финансовые продукты.\n\n"
                        "Ключевые темы отборочного этапа:\n"
                        "• личный бюджет и планирование;\n"
                        "• банковские продукты (вклады, кредиты, карты);\n"
                        "• налоги и обязательные платежи;\n"
                        "• основы инвестирования и риски;\n"
                        "• финансовая безопасность и мошенничество."
                    ),
                    status=MaterialStatus.PUBLISHED,
                    order_index=1,
                ),
            ]
        )
        await session.flush()

        test = Test(
            title="Тест по вводному вебинару",
            description="5 вопросов на понимание базовых понятий.",
            status=MaterialStatus.PUBLISHED,
            order_index=1,
        )
        session.add(test)
        await session.flush()

        session.add_all(
            [
                Question(
                    test_id=test.id, order_index=1,
                    text="Что такое «подушка безопасности»?",
                    options=[
                        "Запас денег на 3–6 месяцев расходов",
                        "Обязательный банковский вклад",
                        "Вид страхового полиса",
                        "Кредитная карта с льготным периодом",
                    ],
                    correct_index=0,
                ),
                Question(
                    test_id=test.id, order_index=2,
                    text="Что из перечисленного — доход?",
                    options=["Оплата аренды", "Зарплата", "Покупка продуктов", "Проценты по кредиту"],
                    correct_index=1,
                ),
                Question(
                    test_id=test.id, order_index=3,
                    text="Диверсификация в инвестициях — это…",
                    options=[
                        "Вложение всех денег в один актив",
                        "Распределение вложений между разными активами",
                        "Отказ от инвестиций",
                        "Снятие всех денег со счёта",
                    ],
                    correct_index=1,
                ),
                Question(
                    test_id=test.id, order_index=4,
                    text="Признак финансового мошенничества:",
                    options=[
                        "Официальный договор",
                        "Гарантия «сверхдохода без риска»",
                        "Лицензия ЦБ",
                        "Прозрачные комиссии",
                    ],
                    correct_index=1,
                ),
                Question(
                    test_id=test.id, order_index=5,
                    text="Что важно сделать перед крупной покупкой в кредит?",
                    options=[
                        "Оценить ежемесячный платёж и переплату",
                        "Взять максимально возможную сумму",
                        "Не читать договор",
                        "Оформить несколько кредитов сразу",
                    ],
                    correct_index=0,
                ),
            ]
        )
        await session.commit()
        log.info("Демо-контент добавлен.")
