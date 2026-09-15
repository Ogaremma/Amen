from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database_initializer import _initialize_stores, initialize_database
from app.services.forebet_draw_store import metadata as forebet_metadata
from app.services.history_store import metadata as history_metadata
from app.services.prediction_store import metadata as prediction_metadata
from app.services.telegram_user_store import metadata as telegram_user_metadata


class DatabaseInitializerTests(unittest.TestCase):
    def test_initializes_all_existing_store_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "amen.sqlite3"
            tables = _initialize_stores(f"sqlite:///{database_path}")

        expected_tables = set(history_metadata.tables)
        expected_tables = expected_tables.union(prediction_metadata.tables)
        expected_tables = expected_tables.union(telegram_user_metadata.tables)
        expected_tables = expected_tables.union(forebet_metadata.tables)
        self.assertEqual(set(tables), expected_tables)

    def test_refuses_render_database_url(self):
        with self.assertRaisesRegex(RuntimeError, "Render PostgreSQL URLs"):
            initialize_database("postgresql://user:password@example.render.com/db")


if __name__ == "__main__":
    unittest.main()
