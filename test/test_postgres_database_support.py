import os
import sys
import tempfile
from types import ModuleType
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.config import get_settings
from backend.app import database
from mlops.data.connection import default_database_target, is_postgres, normalize_postgres_url
from mlops.data.migrate_sqlite_to_postgres import MigrationError, migrate


class PostgresConfigurationTests(unittest.TestCase):
    def test_render_postgres_url_scheme_is_normalized_without_logging(self):
        self.assertTrue(is_postgres("postgres://user:secret@host.example/db"))
        self.assertEqual(
            normalize_postgres_url("postgres://user:secret@host.example/db"),
            "postgresql://user:secret@host.example/db",
        )

    def test_invalid_database_url_is_not_treated_as_a_sqlite_file(self):
        with patch.dict(os.environ, {"DATABASE_URL": " mysql://user:secret@example/db "}, clear=True):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL must use"):
                default_database_target()

    def test_migration_requires_a_real_postgresql_destination_before_import(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.sqlite"
            source.touch()
            with self.assertRaisesRegex(MigrationError, "Destination must be configured"):
                migrate(source, "sqlite://not-a-postgres-url")

    def test_production_requires_database_url_and_local_sqlite_remains_supported(self):
        with patch.dict(os.environ, {"PHISHGUARD_ENV": "production"}, clear=True):
            with self.assertRaisesRegex(ValueError, "requires DATABASE_URL"):
                get_settings()
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {
                "PHISHGUARD_ENV": "test",
                "PHISHGUARD_DATABASE_PATH": str(Path(temp) / "local.sqlite"),
            }, clear=True):
                settings = get_settings()
                self.assertIsNone(settings.database_url)
                self.assertEqual(settings.database_path, Path(temp) / "local.sqlite")

    def test_configured_postgres_never_falls_back_to_sqlite(self):
        with patch.object(database, "DB_URL", "postgresql://user:secret@host.example/db"):
            self.assertEqual(database.get_database_target(), "postgresql://user:secret@host.example/db")
            driver = ModuleType("psycopg")
            driver.connect = Mock(side_effect=OSError("synthetic connection failure"))
            with patch.dict(sys.modules, {"psycopg": driver}):
                with self.assertRaisesRegex(OSError, "synthetic connection failure"):
                    with database.get_connection():
                        pass
            driver.connect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
