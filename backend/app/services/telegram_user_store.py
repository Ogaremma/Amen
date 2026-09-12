from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.services.history_store import _database_url, booking_history


metadata = MetaData()

telegram_bot_users = Table(
    "telegram_bot_users",
    metadata,
    Column("telegram_user_id", BigInteger, primary_key=True),
    Column("telegram_chat_id", BigInteger, nullable=True),
    Column("status", String(16), nullable=False, default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)

Index("idx_telegram_bot_users_status", telegram_bot_users.c.status)


@dataclass(frozen=True)
class TelegramBotUser:
    telegram_user_id: int
    telegram_chat_id: int | None
    status: str
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime


class TelegramUserStore:
    def __init__(self, path: str | None = None, *, database_url: str | None = None) -> None:
        settings = get_settings()
        configured_url = database_url if database_url is not None else (None if path else settings.database_url)
        url = _database_url(path, configured_url)
        options = {"pool_pre_ping": True}
        if url.startswith("sqlite:"):
            options["poolclass"] = NullPool
        self.engine: Engine = create_engine(url, **options)
        self._initialized = False

    def _ensure_schema(self) -> None:
        if not self._initialized:
            metadata.create_all(self.engine)
            self._backfill_from_booking_history()
            self._initialized = True

    def _backfill_from_booking_history(self) -> None:
        inspector = inspect(self.engine)
        if not inspector.has_table("booking_history"):
            return

        now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            existing_ids = set(
                db.execute(select(telegram_bot_users.c.telegram_user_id)).scalars()
            )
            history_ids = set(
                db.execute(select(booking_history.c.telegram_user_id).distinct()).scalars()
            )
            for telegram_user_id in history_ids - existing_ids:
                db.execute(
                    telegram_bot_users.insert().values(
                        telegram_user_id=telegram_user_id,
                        telegram_chat_id=None,
                        status="active",
                        created_at=now,
                        updated_at=now,
                        last_seen_at=now,
                    )
                )

    def upsert_from_auth(
        self,
        telegram_user_id: int,
        *,
        now: datetime | None = None,
    ) -> TelegramBotUser:
        return self._upsert(telegram_user_id, None, now=now)

    def upsert_from_start(
        self,
        telegram_user_id: int,
        telegram_chat_id: int,
        *,
        now: datetime | None = None,
    ) -> TelegramBotUser:
        return self._upsert(telegram_user_id, telegram_chat_id, now=now)

    def _upsert(
        self,
        telegram_user_id: int,
        telegram_chat_id: int | None,
        *,
        now: datetime | None,
    ) -> TelegramBotUser:
        self._ensure_schema()
        current = now or datetime.now(timezone.utc)
        with self.engine.begin() as db:
            existing = db.execute(
                select(telegram_bot_users).where(
                    telegram_bot_users.c.telegram_user_id == telegram_user_id
                )
            ).mappings().first()
            if existing is None:
                db.execute(
                    telegram_bot_users.insert().values(
                        telegram_user_id=telegram_user_id,
                        telegram_chat_id=telegram_chat_id,
                        status="active",
                        created_at=current,
                        updated_at=current,
                        last_seen_at=current,
                    )
                )
            else:
                db.execute(
                    telegram_bot_users.update()
                    .where(
                        telegram_bot_users.c.telegram_user_id == telegram_user_id
                    )
                    .values(
                        telegram_chat_id=telegram_chat_id
                        if telegram_chat_id is not None
                        else existing["telegram_chat_id"],
                        status="active",
                        updated_at=current,
                        last_seen_at=current,
                    )
                )
        return self.get(telegram_user_id)  # type: ignore[return-value]

    def get(self, telegram_user_id: int) -> TelegramBotUser | None:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(telegram_bot_users).where(
                    telegram_bot_users.c.telegram_user_id == telegram_user_id
                )
            ).mappings().first()
        if row is None:
            return None
        return self._record_from_row(row)

    def list_eligible_users(self) -> list[TelegramBotUser]:
        self._ensure_schema()
        with self.engine.connect() as db:
            rows = db.execute(
                select(telegram_bot_users)
                .where(telegram_bot_users.c.status == "active")
                .order_by(telegram_bot_users.c.telegram_user_id)
            ).mappings().all()
        return [self._record_from_row(row) for row in rows]

    def mark_undeliverable(
        self,
        telegram_user_id: int,
        *,
        now: datetime | None = None,
    ) -> None:
        self._ensure_schema()
        current = now or datetime.now(timezone.utc)
        with self.engine.begin() as db:
            db.execute(
                telegram_bot_users.update()
                .where(telegram_bot_users.c.telegram_user_id == telegram_user_id)
                .values(status="undeliverable", updated_at=current)
            )

    @staticmethod
    def _record_from_row(row) -> TelegramBotUser:
        return TelegramBotUser(
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
        )


telegram_user_store = TelegramUserStore()
