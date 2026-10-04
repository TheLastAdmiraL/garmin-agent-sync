from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import garmin_db


class GarminDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "garmin.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_raw_upsert_is_idempotent_and_summary_is_queryable(self) -> None:
        with garmin_db.database(self.db_path) as connection:
            payload = {
                "totalSteps": 12345,
                "restingHeartRate": 51,
                "bodyBatteryLowestValue": 18,
                "bodyBatteryHighestValue": 92,
            }
            self.assertTrue(garmin_db.upsert_raw(connection, "stats", "2026-10-01", payload, "2026-10-01"))
            self.assertFalse(garmin_db.upsert_raw(connection, "stats", "2026-10-01", payload, "2026-10-01"))
            garmin_db.upsert_raw(
                connection,
                "sleep",
                "2026-10-01",
                {
                    "dailySleepDTO": {
                        "sleepTimeSeconds": 28800,
                        "sleepScores": {
                            "deepPercentage": {"value": 12},
                            "overall": {"value": 84},
                        },
                    }
                },
                "2026-10-01",
            )
            garmin_db.refresh_daily_summary(connection, "2026-10-01")
            row = connection.execute(
                "SELECT steps, resting_heart_rate, sleep_seconds, sleep_score FROM daily_summary"
            ).fetchone()
            self.assertEqual(tuple(row), (12345.0, 51.0, 28800.0, 84.0))

    def test_account_export_zip_is_stored_inside_database(self) -> None:
        archive_path = self.root / "export.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("DI_CONNECT/wellness/sample.json", json.dumps({"steps": 42}))
            archive.writestr("DI_CONNECT/fitness/activity.fit", b"FITDATA")

        garmin_db.import_export(self.db_path, archive_path)

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT logical_path, content FROM imported_files ORDER BY logical_path"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "DI_CONNECT/wellness/sample.json")
        self.assertEqual(json.loads(rows[1][1]), {"steps": 42})

    def test_legacy_activity_payload_is_imported(self) -> None:
        legacy = self.root / "legacy"
        details = legacy / "activity-history" / "activities"
        details.mkdir(parents=True)
        (legacy / "activity-history" / "activity-index.json").write_text(
            json.dumps(
                [{"activityId": 99, "activityName": "Test Run", "activityType": {"typeKey": "running"}}]
            ),
            encoding="utf-8",
        )
        (details / "99.json").write_text(
            json.dumps({"activity_id": "99", "data": {"details": {"samples": [1, 2, 3]}}}),
            encoding="utf-8",
        )
        daily = legacy / "daily"
        daily.mkdir()
        (daily / "2026-10-01.md").write_text(
            "# Garmin wellness — 2026-10-01\n\n- Resting HR: 51 bpm\n- Steps: 12345\n- Sleep: 28800 seconds\n",
            encoding="utf-8",
        )

        garmin_db.import_legacy(self.db_path, legacy)

        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM activity_records").fetchone()[0], 1)
            summary = connection.execute(
                "SELECT steps, resting_heart_rate, sleep_seconds FROM daily_summary"
            ).fetchone()
            self.assertEqual(summary, (12345.0, 51.0, 28800.0))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
