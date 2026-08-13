"""Быстрый импорт теста из текста.

Разбирает «человеческий» текст теста в структуру {title, description, questions}.
Формат (терпимый к вариациям маркеров):

    Тест: Кредиты
    Описание: Короткая проверка по теме   (необязательно)

    1. Что такое кредит?
    * Предоставление денег в долг под процент      ← правильный вариант
    - Подарок от банка
    - Способ пассивного заработка

    2. Что проверить перед кредитом?
    * Свою платёжеспособность
    - Курс доллара

Правильный вариант помечается «*», «+», «✅», «✔», «☑» или «[x]» в начале строки
(либо «✅»/«(верно)» в конце строки). Остальные варианты — «-», «•», «–», «—».
Вопрос начинается с номера и точки/скобки: «1.» или «1)».
"""
from __future__ import annotations

import re

_TITLE_RE = re.compile(r"^\s*(?:тест|название|title|тема)\s*[:：]\s*(.+)$", re.IGNORECASE)
_DESC_RE = re.compile(r"^\s*(?:описание|подзаголовок|description)\s*[:：]\s*(.+)$", re.IGNORECASE)
_Q_RE = re.compile(r"^\s*(?:вопрос\s*\d*\s*[:.)]|\d+[.)])\s*(.+)$", re.IGNORECASE)
_OPT_RE = re.compile(r"^\s*(\*|\+|✅|✔|☑|\[.?\]|[-–—•·])️?\s*(.+)$")
_TRAIL_RE = re.compile(
    r"^(.*?)[\s]*(?:✅|✔️?|☑️?|\(\s*верно\s*\)|\(\s*правильн[а-я]*(?:\s+ответ)?\s*\)|\[\s*верно\s*\])\s*$",
    re.IGNORECASE,
)


class TestParseError(ValueError):
    """Понятная ошибка разбора — текст показывается администратору как есть."""


def _is_correct_marker(mk: str) -> bool:
    mk = mk.replace("️", "")
    if mk in ("*", "+", "✅", "✔", "☑"):
        return True
    if mk.startswith("[") and mk.endswith("]"):
        return mk[1:-1].strip().lower() in ("x", "х", "✓", "✔", "v", "+")
    return False


def parse_test_text(text: str) -> dict:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    title = ""
    description: str | None = None
    questions: list[dict] = []
    cur: dict | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Заголовок / описание — только пока не начались вопросы.
        if cur is None and not questions:
            m = _TITLE_RE.match(line)
            if m and not title:
                title = m.group(1).strip()
                continue
            m = _DESC_RE.match(line)
            if m:
                description = m.group(1).strip()
                continue

        # Новый вопрос.
        m = _Q_RE.match(line)
        if m:
            cur = {"text": m.group(1).strip(), "options": [], "correct_index": None}
            questions.append(cur)
            continue

        # Вариант ответа.
        m = _OPT_RE.match(line)
        if m and cur is not None:
            marker, body = m.group(1), m.group(2).strip()
            correct = _is_correct_marker(marker)
            tm = _TRAIL_RE.match(body)
            if tm:
                body = tm.group(1).strip()
                correct = True
            if not body:
                continue
            if correct and cur["correct_index"] is None:
                cur["correct_index"] = len(cur["options"])
            cur["options"].append(body)
            continue

        # Прочая строка: если вопрос ещё без вариантов — считаем продолжением
        # текста вопроса; если заголовка нет и вопросов нет — это заголовок.
        if cur is not None and not cur["options"]:
            cur["text"] = (cur["text"] + " " + line).strip()
        elif not title and not questions:
            title = line

    # --- Валидация с понятными сообщениями ---
    if not title:
        raise TestParseError("Не найдено название теста. Добавьте первой строкой «Тест: …».")
    if not questions:
        raise TestParseError("Не найдено ни одного вопроса. Каждый вопрос начинайте с «1.», «2.» и т.д.")

    for i, q in enumerate(questions, start=1):
        if not q["text"]:
            raise TestParseError(f"Вопрос {i}: пустой текст вопроса.")
        if len(q["options"]) < 2:
            raise TestParseError(
                f"Вопрос {i}: нужно минимум 2 варианта ответа (каждый с новой строки: «* правильный», «- неправильный»)."
            )
        if q["correct_index"] is None:
            raise TestParseError(
                f"Вопрос {i}: не отмечен правильный вариант. Поставьте «*» перед правильным ответом."
            )

    return {
        "title": title,
        "description": description,
        "questions": [
            {"text": q["text"], "options": q["options"], "correct_index": q["correct_index"]}
            for q in questions
        ],
    }
