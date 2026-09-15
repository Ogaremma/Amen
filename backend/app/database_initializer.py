from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import inspect

from app.config.settings import get_settings
from app.services.forebet_draw_store import (
    ForebetDrawStore,
    metadata as forebet_metadata,
)
from app.services.history_store import HistoryStore, metadata as history_metadata
from app.services.prediction_store import (
    PredictionStore,
    metadata as prediction_metadata,
)
from app.services.telegram_user_store import (
    TelegramUserStore,
    metadata as telegram_user_metadata,
)


def _validate_neon_url(database_url: Optional[str]) -> None:
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    hostname = (parsed.hostname or "").lower()
    if ".render.com" in hostname:
        raise RuntimeError("Render PostgreSQL URLs are not allowed")
    if not (hostname.endswith(".neon.tech") or hostname.endswith(".neon.build")):
        raise RuntimeError("DATABASE_URL must point to a Neon PostgreSQL database")


def _initialize_stores(database_url: str) -> list[str]:
    history_store = HistoryStore(database_url=database_url)
    prediction_store = PredictionStore(database_url=database_url)
    telegram_user_store = TelegramUserStore(database_url=database_url)
    forebet_draw_store = ForebetDrawStore(database_url=database_url)
    stores = (
        history_store,
        prediction_store,
        telegram_user_store,
        forebet_draw_store,
    )

    try:
        history_store._ensure_schema()
        prediction_store._ensure_schema()
        telegram_user_store._ensure_schema()
        forebet_draw_store._ensure()

        expected_tables = set(history_metadata.tables)
        expected_tables = expected_tables.union(prediction_metadata.tables)
        expected_tables = expected_tables.union(telegram_user_metadata.tables)
        expected_tables = expected_tables.union(forebet_metadata.tables)
        actual_tables = set(inspect(history_store.engine).get_table_names())
        missing_tables = expected_tables.difference(actual_tables)
        if missing_tables:
            raise RuntimeError(
                "Database initialization incomplete; missing tables: "
                + ", ".join(sorted(missing_tables))
            )
        return sorted(expected_tables)
    finally:
        for store in stores:
            store.engine.dispose()


def initialize_database(database_url: Optional[str]) -> list[str]:
    _validate_neon_url(database_url)
    return _initialize_stores(database_url)


def main() -> None:
    tables = initialize_database(get_settings().database_url)
    print("Initialized Neon PostgreSQL tables:")
    for table_name in tables:
        print(f"  {table_name}")


if __name__ == "__main__":
    main()
