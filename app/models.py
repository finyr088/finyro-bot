"""Модели данных: пользователи, оплаты, материалы, тесты, прогресс, поддержка, логи."""
from __future__ import annotations

from datetime import datetime

from datetime import date

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


# --- Статусы (строковые константы, чтобы не тянуть Enum в SQLite) ---

class PaymentStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MaterialKind:
    VIDEO = "video"      # вебинар
    THEORY = "theory"    # тема теории


class MaterialStatus:
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Доступ к материалам курса
    access_active: Mapped[bool] = mapped_column(Boolean, default=False)
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Геймификация: серия активных дней подряд
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Реферальная программа
    referred_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )                                                      # кто пригласил этого ученика
    referral_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)  # комиссия за него уже начислена
    referral_earned: Mapped[int] = mapped_column(Integer, default=0)         # заработал как пригласивший, ₽
    referral_paid_out: Mapped[int] = mapped_column(Integer, default=0)       # из них уже выплачено, ₽

    # Единый «якорный» блок в чате ученика — id последнего сообщения бота,
    # которое переиспользуется (редактируется), чтобы чат не разрастался.
    anchor_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Защита от шеринга: одно активное устройство + «перегрев» при коллизии.
    active_device: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    payments: Mapped[list["Payment"]] = relationship(back_populates="user")

    @property
    def full_name(self) -> str:
        parts = [self.first_name or "", self.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or (self.username and f"@{self.username}") or f"id{self.telegram_id}"

    def has_access(self, now: datetime | None = None) -> bool:
        if not self.access_active:
            return False
        if self.access_expires_at is not None:
            now = now or utcnow()
            if self.access_expires_at < now:
                return False
        return True


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PENDING, index=True)
    proof_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amount: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="payments")


class Section(Base):
    """Раздел вебинаров верхнего уровня, напр. «Отборочный этап»."""
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Topic(Base):
    """Тема внутри раздела; содержит видео."""
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Attachment(Base):
    """Вложение к видео: презентация, полезный материал или ссылка."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text)          # /media/<файл> или внешняя ссылка
    kind: Mapped[str] = mapped_column(String(16), default="file")  # presentation | file | link
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default=MaterialKind.VIDEO, index=True)
    # Видео может принадлежать теме (topic_id); теория — нет.
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)   # текст теории
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # HLS-ссылка для видео
    status: Mapped[str] = mapped_column(String(16), default=MaterialStatus.DRAFT, index=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def is_visible(self, now: datetime | None = None) -> bool:
        """Опубликован ли материал для ученика прямо сейчас."""
        if self.status == MaterialStatus.PUBLISHED:
            return True
        if self.status == MaterialStatus.SCHEDULED and self.publish_at is not None:
            return self.publish_at <= (now or utcnow())
        return False


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=MaterialStatus.DRAFT, index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", order_by="Question.order_index"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)   # список вариантов ответа
    correct_index: Mapped[int] = mapped_column(Integer, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    test: Mapped["Test"] = relationship(back_populates="questions")


class TestAttempt(Base):
    __tablename__ = "test_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MaterialProgress(Base):
    __tablename__ = "material_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # секунда, на которой остановились


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    from_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoginCode(Base):
    """Одноразовый код для входа на сайт в браузере (когда Telegram WebApp недоступен)."""
    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(8))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    """Событие расписания/календаря: вебинар, дедлайн регистрации, олимпиада и т.п."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # kind: event | webinar | deadline | registration | olympiad
    kind: Mapped[str] = mapped_column(String(32), default="event")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    """Редактируемые из админки настройки (ключ→значение). Напр. цена курса."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AccessFingerprint(Base):
    """Отпечаток доступа: с каких IP/устройств заходит ученик — для детекции
    шеринга одного оплаченного аккаунта несколькими людьми."""
    __tablename__ = "access_fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    device: Mapped[str] = mapped_column(String(200), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    hits: Mapped[int] = mapped_column(Integer, default=1)


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
