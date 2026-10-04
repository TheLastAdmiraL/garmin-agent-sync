from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


FUNCTIONS_ROOT = Path(__file__).resolve().parents[1]
if str(FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_ROOT))

from cloud_sync import (  # noqa: E402
    Collection,
    MAX_CELL_CHARS,
    RawRecord,
    activity_intervals_text,
    chunks,
    hourly_hr_text,
    raw_rows,
)
from sync_service import coalesce_ranges, run_sync  # noqa: E402


class FakeStore:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}
        self.readme_statuses: list[str] = []
        self.sync_logs: list[list[object]] = []

    def ensure_tabs(self, titles):
        return None

    def write_readme(self, status, *args):
        self.readme_statuses.append(status)

    def read_state(self):
        return dict(self.state)

    def open_retry_days(self, limit=5):
        return []

    def existing_record_ids(self, title):
        return set()

    def replace_chunked_records(self, *args, **kwargs):
        incoming = args[2]
        return len(incoming), 0, []

    def insert_new_chunked_records(self, *args, **kwargs):
        incoming = args[2]
        return len(incoming), 0, []

    def upsert_rows(self, *args, **kwargs):
        incoming = args[2]
        return len(incoming), len(incoming)

    def append_changes(self, incoming):
        return None

    def update_errors(self, failures, successful_keys):
        return None

    def write_state(self, values):
        self.state.update({key: value for key, (value, _notes) in values.items()})

    def append_sync_log(self, row):
        self.sync_logs.append(row)


class FakeCollector:
    def __init__(self, token: str) -> None:
        self.token = token

    def collect_snapshots(self, observed_on):
        return Collection()

    def collect_daily(self, days):
        result = Collection()
        for day in days:
            day_text = day.isoformat()
            result.raw_records.append(
                RawRecord("daily", "stats", day_text, day_text, {"totalSteps": 1000}, "2026-10-03T10:00:00+05:30")
            )
        return result

    def collect_activities(self, ranges, existing_original_activity_ids=None):
        return Collection()

    def refreshed_token(self):
        return self.token


class FailingCollector:
    def __init__(self, token: str) -> None:
        raise RuntimeError("login failed")


class CloudTransformTests(unittest.TestCase):
    def test_chunked_json_round_trips_without_data_loss(self) -> None:
        payload = {"samples": [{"timestamp": index, "heartRate": 100 + index % 20} for index in range(5000)]}
        record = RawRecord("activity", "details", "42", "2026-10-01", payload, "2026-10-01T12:00:00+05:30")
        rows = raw_rows(record)
        reconstructed = "".join(row[10] for row in sorted(rows, key=lambda row: row[8]))
        self.assertEqual(json.loads(reconstructed), payload)
        self.assertTrue(all(len(row[10]) <= MAX_CELL_CHARS for row in rows))

    def test_hourly_hr_uses_actual_samples_and_marks_activity(self) -> None:
        start_ms = int(datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        payload = {"heartRateValues": [[start_ms, 80], [start_ms + 60_000, 100], [start_ms + 120_000, None]]}
        activities = [
            {
                "activityId": 7,
                "startTimeLocal": "2026-10-01T10:20:00",
                "duration": 3600,
                "activityType": {"typeKey": "running"},
                "activityName": "Morning Run",
            }
        ]
        rendered = hourly_hr_text("2026-10-01", payload, activities)
        self.assertIn("10-11: 90.0 bpm (n=2) [ACTIVITY:running]", rendered)
        self.assertIn("11-12: NA (n=0) [ACTIVITY:running]", rendered)
        self.assertEqual(len(rendered.split("; ")), 24)
        self.assertIn("10:20-11:20 running", activity_intervals_text(activities))

    def test_ranges_are_coalesced_for_fewer_garmin_calls(self) -> None:
        days = {
            date(2026, 9, 1),
            date(2026, 9, 2),
            date(2026, 9, 4),
            date(2026, 9, 5),
            date(2026, 9, 6),
        }
        self.assertEqual(
            coalesce_ranges(days),
            [(date(2026, 9, 1), date(2026, 9, 2)), (date(2026, 9, 4), date(2026, 9, 6))],
        )

    def test_chunks_always_return_at_least_one_cell(self) -> None:
        self.assertEqual(chunks(""), [""])

    def test_orchestrator_finishes_with_commit_marker(self) -> None:
        store = FakeStore()
        result = run_sync(
            spreadsheet_id="sheet",
            token_json="{}",
            backfill_start="2026-10-01",
            backfill_batch_days=7,
            refresh_days=3,
            today=date(2026, 10, 3),
            store=store,
            collector_factory=FakeCollector,
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(store.readme_statuses, ["RUNNING", "SUCCESS"])
        self.assertEqual(store.sync_logs[-1][4], "SUCCESS")

    def test_orchestrator_replaces_running_with_failed(self) -> None:
        store = FakeStore()
        with self.assertRaisesRegex(RuntimeError, "login failed"):
            run_sync(
                spreadsheet_id="sheet",
                token_json="{}",
                backfill_start="2026-10-01",
                backfill_batch_days=7,
                refresh_days=3,
                today=date(2026, 10, 3),
                store=store,
                collector_factory=FailingCollector,
            )
        self.assertEqual(store.readme_statuses, ["RUNNING", "FAILED"])
        self.assertEqual(store.sync_logs[-1][4], "FAILED")


if __name__ == "__main__":
    unittest.main()
