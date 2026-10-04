#!/usr/bin/env python3
"""Resumable, read-only Garmin Connect archive backed by one SQLite database.

The database keeps complete JSON responses as the source of truth and also
maintains small relational indexes that make common analysis queries simple.
Garmin credentials and session tokens are never stored in the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sqlite3
import sys
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from token_store import load as load_token
from token_store import save as save_token
from functions.redaction import safe_error


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "garmin" / "garmin.sqlite3"
DEFAULT_LEGACY_DIR = ROOT / "garmin"
SCHEMA_VERSION = 1
REQUEST_DELAY_SECONDS = 0.35
RETRIES = 3


# These calls preserve Garmin's full per-day response. Some overlap is
# deliberate: Garmin exposes different details through summary and dedicated
# endpoints, and retaining both avoids discarding potentially useful fields.
DAILY_ENDPOINTS: dict[str, tuple[str, str]] = {
    "stats": ("get_stats", "day"),
    "user_summary": ("get_user_summary", "day"),
    "stats_and_body": ("get_stats_and_body", "day"),
    "steps": ("get_steps_data", "day"),
    "heart_rates": ("get_heart_rates", "day"),
    "resting_heart_rate": ("get_rhr_day", "day"),
    "sleep": ("get_sleep_data", "day"),
    "sleep_daily": ("get_sleep_daily", "same_day_range"),
    "all_day_stress": ("get_all_day_stress", "day"),
    "stress": ("get_stress_data", "day"),
    "body_battery": ("get_body_battery", "same_day_range"),
    "body_battery_events": ("get_body_battery_events", "day"),
    "respiration": ("get_respiration_data", "day"),
    "spo2": ("get_spo2_data", "day"),
    "hrv": ("get_hrv_data", "day"),
    "hrv_range": ("get_hrv_data_range", "same_day_range"),
    "training_readiness": ("get_training_readiness", "day"),
    "morning_training_readiness": ("get_morning_training_readiness", "day"),
    "training_status": ("get_training_status", "day"),
    "max_metrics": ("get_max_metrics", "day"),
    "max_metrics_range": ("get_max_metrics_range", "same_day_range"),
    "fitness_age": ("get_fitnessage_data", "day"),
    "intensity_minutes": ("get_intensity_minutes_data", "day"),
    "floors": ("get_floors", "day"),
    "calories": ("get_calories_daily", "same_day_range"),
    "hydration": ("get_hydration_data", "day"),
    "body_composition": ("get_body_composition", "same_day_range"),
    "weigh_ins": ("get_daily_weigh_ins", "day"),
    "blood_pressure": ("get_blood_pressure", "same_day_range"),
    "endurance_score": ("get_endurance_score", "same_day_range"),
    "hill_score": ("get_hill_score", "same_day_range"),
    "all_day_events": ("get_all_day_events", "day"),
    "lifestyle_logging": ("get_lifestyle_logging_data", "day"),
    "nutrition_food_log": ("get_nutrition_daily_food_log", "day"),
    "nutrition_meals": ("get_nutrition_daily_meals", "day"),
    "nutrition_settings": ("get_nutrition_daily_settings", "day"),
    "menstrual_cycle": ("get_menstrual_data_for_date", "day"),
}


SNAPSHOT_ENDPOINTS: dict[str, tuple[str, tuple[Any, ...]]] = {
    "user_profile": ("get_user_profile", ()),
    "user_profile_settings": ("get_userprofile_settings", ()),
    "unit_system": ("get_unit_system", ()),
    "devices": ("get_devices", ()),
    "last_used_device": ("get_device_last_used", ()),
    "primary_training_device": ("get_primary_training_device", ()),
    "heart_rate_zones": ("get_heart_rate_zones", ()),
    "power_zones": ("get_power_zones", ()),
    "race_predictions": ("get_race_predictions", ()),
    "lactate_threshold": ("get_lactate_threshold", ()),
    "pregnancy_summary": ("get_pregnancy_summary", ()),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def iter_days(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT NOT NULL,
            requests_ok INTEGER NOT NULL DEFAULT 0,
            requests_failed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            dataset TEXT PRIMARY KEY,
            last_successful_date TEXT,
            last_attempt_at TEXT,
            last_status TEXT,
            last_error TEXT
        );

        CREATE TABLE IF NOT EXISTS raw_records (
            dataset TEXT NOT NULL,
            record_key TEXT NOT NULL,
            observed_on TEXT,
            fetched_at TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            PRIMARY KEY (dataset, record_key)
        );
        CREATE INDEX IF NOT EXISTS raw_records_observed
            ON raw_records(observed_on, dataset);

        CREATE TABLE IF NOT EXISTS daily_summary (
            day TEXT PRIMARY KEY,
            steps REAL,
            resting_heart_rate REAL,
            average_stress REAL,
            body_battery_low REAL,
            body_battery_high REAL,
            sleep_seconds REAL,
            sleep_score REAL,
            hrv_overnight REAL,
            training_readiness REAL,
            average_spo2 REAL,
            average_respiration REAL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activities (
            activity_id TEXT PRIMARY KEY,
            start_time TEXT,
            activity_type TEXT,
            name TEXT,
            distance_m REAL,
            duration_s REAL,
            summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
            content_sha256 TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activity_records (
            activity_id TEXT NOT NULL,
            dataset TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            content_sha256 TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (activity_id, dataset),
            FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS imported_files (
            logical_path TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            media_type TEXT,
            byte_length INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            content BLOB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS imported_files_hash ON imported_files(content_sha256);

        CREATE TABLE IF NOT EXISTS sync_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            dataset TEXT NOT NULL,
            record_key TEXT,
            occurred_at TEXT NOT NULL,
            error_type TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES sync_runs(id) ON DELETE CASCADE
        );
        """
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        create_schema(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()


def authenticated_client() -> Garmin:
    try:
        client = Garmin()
        client.login(load_token())
        return client
    except Exception as error:
        sys.exit(
            f"Could not use the saved Garmin login ({type(error).__name__}: {error}). "
            "Run sync_garmin.py --login in an interactive terminal."
        )


def retrieve(method: Callable[..., Any], *args: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            value = method(*args)
            time.sleep(REQUEST_DELAY_SECONDS)
            return value
        except Exception as error:
            last_error = error
            if attempt + 1 < RETRIES:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def upsert_raw(
    connection: sqlite3.Connection,
    dataset: str,
    record_key: str,
    payload: Any,
    observed_on: str | None = None,
) -> bool:
    serialized = json_text(payload)
    digest = content_hash(serialized)
    existing = connection.execute(
        "SELECT content_sha256 FROM raw_records WHERE dataset=? AND record_key=?",
        (dataset, record_key),
    ).fetchone()
    changed = existing is None or existing["content_sha256"] != digest
    connection.execute(
        """
        INSERT INTO raw_records(dataset, record_key, observed_on, fetched_at, content_sha256, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset, record_key) DO UPDATE SET
            observed_on=excluded.observed_on,
            fetched_at=excluded.fetched_at,
            content_sha256=excluded.content_sha256,
            payload_json=excluded.payload_json
        """,
        (dataset, record_key, observed_on, utc_now(), digest, serialized),
    )
    return changed


def recursively_find(value: Any, names: Iterable[str]) -> Any:
    wanted = set(names)
    if isinstance(value, dict):
        for key, item in value.items():
            if key in wanted and item is not None and not isinstance(item, (dict, list)):
                return item
        for item in value.values():
            found = recursively_find(item, wanted)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = recursively_find(item, wanted)
            if found is not None:
                return found
    return None


def nested_value(value: Any, *path: str | int) -> Any:
    for part in path:
        if isinstance(part, int) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
        elif isinstance(part, str) and isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def refresh_daily_summary(connection: sqlite3.Connection, day: str) -> None:
    rows = connection.execute(
        "SELECT dataset, payload_json FROM raw_records WHERE observed_on=?",
        (day,),
    ).fetchall()
    payloads = {row["dataset"]: json.loads(row["payload_json"]) for row in rows}

    def find(dataset: str, *names: str) -> float | None:
        return number(recursively_find(payloads.get(dataset), names))

    sleep_score = number(
        nested_value(
            payloads.get("sleep"),
            "dailySleepDTO",
            "sleepScores",
            "overall",
            "value",
        )
    )

    connection.execute(
        """
        INSERT INTO daily_summary(
            day, steps, resting_heart_rate, average_stress,
            body_battery_low, body_battery_high, sleep_seconds, sleep_score,
            hrv_overnight, training_readiness, average_spo2,
            average_respiration, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day) DO UPDATE SET
            steps=COALESCE(excluded.steps, daily_summary.steps),
            resting_heart_rate=COALESCE(excluded.resting_heart_rate, daily_summary.resting_heart_rate),
            average_stress=COALESCE(excluded.average_stress, daily_summary.average_stress),
            body_battery_low=COALESCE(excluded.body_battery_low, daily_summary.body_battery_low),
            body_battery_high=COALESCE(excluded.body_battery_high, daily_summary.body_battery_high),
            sleep_seconds=COALESCE(excluded.sleep_seconds, daily_summary.sleep_seconds),
            sleep_score=COALESCE(excluded.sleep_score, daily_summary.sleep_score),
            hrv_overnight=COALESCE(excluded.hrv_overnight, daily_summary.hrv_overnight),
            training_readiness=COALESCE(excluded.training_readiness, daily_summary.training_readiness),
            average_spo2=COALESCE(excluded.average_spo2, daily_summary.average_spo2),
            average_respiration=COALESCE(excluded.average_respiration, daily_summary.average_respiration),
            updated_at=excluded.updated_at
        """,
        (
            day,
            find("stats", "totalSteps", "steps"),
            find("stats", "restingHeartRate", "restingHR"),
            find("all_day_stress", "averageStressLevel", "avgStressLevel"),
            find("stats", "bodyBatteryLowestValue", "bodyBatteryLow"),
            find("stats", "bodyBatteryHighestValue", "bodyBatteryHigh"),
            find("sleep", "sleepTimeSeconds", "sleepDuration"),
            sleep_score,
            find("hrv", "lastNightAvg", "lastNightAverage"),
            find("training_readiness", "score", "trainingReadinessScore"),
            find("spo2", "averageSpO2", "avgSpO2", "averageSpo2"),
            find("respiration", "avgWakingRespirationValue", "averageRespirationValue"),
            utc_now(),
        ),
    )


def update_state(
    connection: sqlite3.Connection,
    dataset: str,
    status: str,
    successful_day: str | None = None,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO sync_state(dataset, last_successful_date, last_attempt_at, last_status, last_error)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(dataset) DO UPDATE SET
            last_successful_date=COALESCE(excluded.last_successful_date, sync_state.last_successful_date),
            last_attempt_at=excluded.last_attempt_at,
            last_status=excluded.last_status,
            last_error=excluded.last_error
        """,
        (dataset, successful_day, utc_now(), status, error),
    )


def record_error(
    connection: sqlite3.Connection,
    run_id: int,
    dataset: str,
    record_key: str | None,
    error: Exception,
) -> None:
    message = safe_error(error)
    connection.execute(
        "INSERT INTO sync_errors(run_id, dataset, record_key, occurred_at, error_type, message) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (run_id, dataset, record_key, utc_now(), type(error).__name__, message),
    )
    update_state(connection, dataset, "error", error=f"{type(error).__name__}: {message}")


def call_daily(client: Garmin, method_name: str, argument_style: str, day: str) -> Any:
    method = getattr(client, method_name)
    if argument_style == "same_day_range":
        return retrieve(method, day, day)
    return retrieve(method, day)


def dataset_start(
    connection: sqlite3.Connection,
    dataset: str,
    requested_start: date | None,
    end: date,
    refresh_days: int,
) -> date | None:
    if requested_start is not None:
        return requested_start
    row = connection.execute(
        "SELECT last_successful_date FROM sync_state WHERE dataset=?",
        (dataset,),
    ).fetchone()
    if row is None or not row["last_successful_date"]:
        return None
    last = date.fromisoformat(row["last_successful_date"])
    return min(end, last + timedelta(days=1) - timedelta(days=max(0, refresh_days)))


def sync_daily(
    connection: sqlite3.Connection,
    client: Garmin,
    run_id: int,
    requested_start: date | None,
    end: date,
    refresh_days: int,
) -> tuple[int, int, int]:
    ok = failed = changed = 0
    touched_days: set[str] = set()
    for dataset, (method_name, style) in DAILY_ENDPOINTS.items():
        if not hasattr(client, method_name):
            error = RuntimeError(f"installed garminconnect has no {method_name} method")
            record_error(connection, run_id, dataset, None, error)
            failed += 1
            continue
        start = dataset_start(connection, dataset, requested_start, end, refresh_days)
        if start is None:
            raise RuntimeError(
                "This database has no daily sync cursor. Provide --start YYYY-MM-DD for the first sync."
            )
        retry_rows = connection.execute(
            """
            SELECT DISTINCT record_key FROM sync_errors
            WHERE dataset=? AND record_key GLOB '????-??-??'
            """,
            (dataset,),
        ).fetchall()
        days_to_fetch = {current.isoformat() for current in iter_days(start, end)}
        days_to_fetch.update(
            row["record_key"] for row in retry_rows if row["record_key"] <= end.isoformat()
        )
        for day in sorted(days_to_fetch):
            try:
                payload = call_daily(client, method_name, style, day)
                changed += int(upsert_raw(connection, dataset, day, payload, day))
                connection.execute(
                    "DELETE FROM sync_errors WHERE dataset=? AND record_key=?",
                    (dataset, day),
                )
                update_state(connection, dataset, "ok", day)
                touched_days.add(day)
                ok += 1
            except Exception as error:
                record_error(connection, run_id, dataset, day, error)
                failed += 1
            connection.commit()
        print(f"{dataset}: {start.isoformat()} through {end.isoformat()}", flush=True)

    for day in sorted(touched_days):
        refresh_daily_summary(connection, day)
    return ok, failed, changed


def sync_snapshots(
    connection: sqlite3.Connection, client: Garmin, run_id: int
) -> tuple[int, int, int]:
    ok = failed = changed = 0
    for dataset, (method_name, args) in SNAPSHOT_ENDPOINTS.items():
        if not hasattr(client, method_name):
            continue
        try:
            payload = retrieve(getattr(client, method_name), *args)
            snapshot_day = date.today().isoformat()
            changed += int(
                upsert_raw(
                    connection,
                    f"snapshot:{dataset}",
                    snapshot_day,
                    payload,
                    snapshot_day,
                )
            )
            update_state(connection, f"snapshot:{dataset}", "ok", snapshot_day)
            ok += 1
        except Exception as error:
            record_error(connection, run_id, f"snapshot:{dataset}", "current", error)
            failed += 1
    connection.commit()
    return ok, failed, changed


ACTIVITY_ENDPOINTS: dict[str, str] = {
    "summary": "get_activity",
    "details": "get_activity_details",
    "heart_rate_time_in_zones": "get_activity_hr_in_timezones",
    "power_time_in_zones": "get_activity_power_in_timezones",
    "splits": "get_activity_splits",
    "typed_splits": "get_activity_typed_splits",
    "split_summaries": "get_activity_split_summaries",
    "weather": "get_activity_weather",
    "gear": "get_activity_gear",
    "exercise_sets": "get_activity_exercise_sets",
}


def all_activities(client: Garmin) -> list[dict[str, Any]]:
    total = retrieve(client.count_activities)
    activities: list[dict[str, Any]] = []
    page_size = 100
    for offset in range(0, int(total), page_size):
        page = retrieve(client.get_activities, offset, min(page_size, int(total) - offset))
        if not isinstance(page, list):
            raise RuntimeError(f"unexpected activity list at offset {offset}")
        activities.extend(item for item in page if isinstance(item, dict))
        if not page:
            break
    return activities


def upsert_activity(connection: sqlite3.Connection, activity: dict[str, Any]) -> tuple[str, bool]:
    activity_id = str(activity.get("activityId") or "")
    if not activity_id:
        raise ValueError("activity has no activityId")
    serialized = json_text(activity)
    digest = content_hash(serialized)
    existing = connection.execute(
        "SELECT content_sha256 FROM activities WHERE activity_id=?", (activity_id,)
    ).fetchone()
    changed = existing is None or existing["content_sha256"] != digest
    activity_type = activity.get("activityType")
    if isinstance(activity_type, dict):
        activity_type = activity_type.get("typeKey") or activity_type.get("typeId")
    connection.execute(
        """
        INSERT INTO activities(
            activity_id, start_time, activity_type, name, distance_m, duration_s,
            summary_json, content_sha256, fetched_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(activity_id) DO UPDATE SET
            start_time=excluded.start_time,
            activity_type=excluded.activity_type,
            name=excluded.name,
            distance_m=excluded.distance_m,
            duration_s=excluded.duration_s,
            summary_json=excluded.summary_json,
            content_sha256=excluded.content_sha256,
            fetched_at=excluded.fetched_at
        """,
        (
            activity_id,
            activity.get("startTimeLocal") or activity.get("startTimeGMT"),
            str(activity_type or ""),
            activity.get("activityName"),
            number(activity.get("distance")),
            number(activity.get("duration")),
            serialized,
            digest,
            utc_now(),
        ),
    )
    return activity_id, changed


def sync_activities(
    connection: sqlite3.Connection,
    client: Garmin,
    run_id: int,
    refresh: bool,
) -> tuple[int, int, int]:
    ok = failed = changed = 0
    try:
        activities = all_activities(client)
    except Exception as error:
        record_error(connection, run_id, "activities:index", None, error)
        return ok, failed + 1, changed

    for index, activity in enumerate(activities, start=1):
        try:
            activity_id, summary_changed = upsert_activity(connection, activity)
            changed += int(summary_changed)
        except Exception as error:
            record_error(connection, run_id, "activities:index", None, error)
            failed += 1
            continue
        for dataset, method_name in ACTIVITY_ENDPOINTS.items():
            if not hasattr(client, method_name):
                continue
            already_stored = connection.execute(
                "SELECT 1 FROM activity_records WHERE activity_id=? AND dataset=?",
                (activity_id, dataset),
            ).fetchone()
            if already_stored and not refresh:
                continue
            try:
                payload = retrieve(getattr(client, method_name), activity_id)
                serialized = json_text(payload)
                digest = content_hash(serialized)
                connection.execute(
                    """
                    INSERT INTO activity_records(activity_id, dataset, payload_json, content_sha256, fetched_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(activity_id, dataset) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        content_sha256=excluded.content_sha256,
                        fetched_at=excluded.fetched_at
                    """,
                    (activity_id, dataset, serialized, digest, utc_now()),
                )
                connection.execute(
                    "DELETE FROM sync_errors WHERE dataset=? AND record_key=?",
                    (f"activity:{dataset}", activity_id),
                )
                ok += 1
            except Exception as error:
                record_error(connection, run_id, f"activity:{dataset}", activity_id, error)
                failed += 1
        original_path = f"activities/original/{activity_id}.zip"
        original_stored = connection.execute(
            "SELECT 1 FROM imported_files WHERE logical_path IN (?, ?)",
            (original_path, f"legacy/activity-history/original/{activity_id}.zip"),
        ).fetchone()
        if refresh or not original_stored:
            try:
                original = retrieve(
                    client.download_activity,
                    activity_id,
                    Garmin.ActivityDownloadFormat.ORIGINAL,
                )
                if not isinstance(original, bytes):
                    raise TypeError("Garmin returned non-binary original activity data")
                changed += int(
                    store_imported_file(
                        connection,
                        original_path,
                        "garmin-connect-incremental",
                        original,
                    )
                )
                connection.execute(
                    "DELETE FROM sync_errors WHERE dataset='activity:original' AND record_key=?",
                    (activity_id,),
                )
                ok += 1
            except Exception as error:
                record_error(connection, run_id, "activity:original", activity_id, error)
                failed += 1
        connection.commit()
        print(f"activities: {index}/{len(activities)}", flush=True)
    update_state(connection, "activities", "ok", date.today().isoformat())
    return ok, failed, changed


def run_sync(
    db_path: Path,
    start: date | None,
    end: date,
    refresh_days: int,
    refresh_activities: bool,
    skip_daily: bool,
    skip_activities: bool,
) -> None:
    if start and start > end:
        sys.exit("--start cannot be after --end")
    client = authenticated_client()
    with database(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO sync_runs(started_at, start_date, end_date, status) VALUES(?, ?, ?, 'running')",
            (utc_now(), start.isoformat() if start else None, end.isoformat()),
        )
        run_id = int(cursor.lastrowid)
        totals = [0, 0, 0]
        try:
            result = sync_snapshots(connection, client, run_id)
            totals = [a + b for a, b in zip(totals, result)]
            if not skip_daily:
                result = sync_daily(connection, client, run_id, start, end, refresh_days)
                totals = [a + b for a, b in zip(totals, result)]
            if not skip_activities:
                result = sync_activities(connection, client, run_id, refresh_activities)
                totals = [a + b for a, b in zip(totals, result)]
            status = "completed_with_errors" if totals[1] else "completed"
        except Exception:
            status = "failed"
            raise
        finally:
            connection.execute(
                "UPDATE sync_runs SET completed_at=?, status=?, requests_ok=?, requests_failed=? WHERE id=?",
                (utc_now(), status, totals[0], totals[1], run_id),
            )
            connection.commit()
            try:
                save_token(client.client.dumps())
            except Exception as error:
                print(f"Warning: could not refresh encrypted session token ({type(error).__name__}).", file=sys.stderr)
        print(
            f"Sync {status}: {totals[0]} successful calls, {totals[1]} failed calls, "
            f"{totals[2]} changed records. Database: {db_path}",
            flush=True,
        )


def file_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def store_imported_file(
    connection: sqlite3.Connection,
    logical_path: str,
    source_name: str,
    payload: bytes,
) -> bool:
    digest = content_hash(payload)
    existing = connection.execute(
        "SELECT content_sha256 FROM imported_files WHERE logical_path=?", (logical_path,)
    ).fetchone()
    changed = existing is None or existing["content_sha256"] != digest
    connection.execute(
        """
        INSERT INTO imported_files(
            logical_path, source_name, media_type, byte_length,
            content_sha256, imported_at, content
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(logical_path) DO UPDATE SET
            source_name=excluded.source_name,
            media_type=excluded.media_type,
            byte_length=excluded.byte_length,
            content_sha256=excluded.content_sha256,
            imported_at=excluded.imported_at,
            content=excluded.content
        """,
        (
            logical_path.replace("\\", "/"),
            source_name,
            file_media_type(logical_path),
            len(payload),
            digest,
            utc_now(),
            payload,
        ),
    )
    return changed


def import_export(db_path: Path, source: Path) -> None:
    source = source.resolve()
    if not source.exists():
        sys.exit(f"Import source does not exist: {source}")
    total = changed = 0
    with database(db_path) as connection:
        if source.is_file() and zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    payload = archive.read(info)
                    changed += int(store_imported_file(connection, info.filename, source.name, payload))
                    total += 1
                    if total % 250 == 0:
                        connection.commit()
                        print(f"Imported {total} files...", flush=True)
        elif source.is_dir():
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                logical_path = path.relative_to(source).as_posix()
                changed += int(store_imported_file(connection, logical_path, source.name, path.read_bytes()))
                total += 1
                if total % 250 == 0:
                    connection.commit()
                    print(f"Imported {total} files...", flush=True)
        else:
            payload = source.read_bytes()
            changed += int(store_imported_file(connection, source.name, source.name, payload))
            total = 1
    print(f"Import complete: {total} files scanned, {changed} added or changed. Database: {db_path}")


def import_legacy(db_path: Path, source: Path) -> None:
    """Move the repository's existing activity JSON into the relational archive."""
    activity_index = source / "activity-history" / "activity-index.json"
    detail_dir = source / "activity-history" / "activities"
    imported = details = daily_rows = 0
    with database(db_path) as connection:
        if activity_index.exists():
            payload = json.loads(activity_index.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict) and item.get("activityId"):
                        upsert_activity(connection, item)
                        imported += 1
        if detail_dir.exists():
            for path in detail_dir.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                activity_id = str(payload.get("activity_id") or path.stem)
                if not connection.execute(
                    "SELECT 1 FROM activities WHERE activity_id=?", (activity_id,)
                ).fetchone():
                    upsert_activity(connection, {"activityId": activity_id, "activityName": "Imported activity"})
                for dataset, value in payload.get("data", {}).items():
                    serialized = json_text(value)
                    connection.execute(
                        """
                        INSERT INTO activity_records(activity_id, dataset, payload_json, content_sha256, fetched_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(activity_id, dataset) DO UPDATE SET
                            payload_json=excluded.payload_json,
                            content_sha256=excluded.content_sha256,
                            fetched_at=excluded.fetched_at
                        """,
                        (activity_id, dataset, serialized, content_hash(serialized), utc_now()),
                    )
                    details += 1
        daily_dir = source / "daily"
        if daily_dir.exists():
            for path in sorted(daily_dir.glob("????-??-??.md")):
                day = path.stem
                fields: dict[str, float | None] = {}
                for line in path.read_text(encoding="utf-8").splitlines():
                    match = re.fullmatch(r"- ([^:]+):\s*(.*)", line)
                    if not match:
                        continue
                    numeric = re.search(r"-?\d+(?:\.\d+)?", match.group(2))
                    fields[match.group(1)] = float(numeric.group()) if numeric else None
                upsert_raw(connection, "legacy:daily_markdown", day, fields, day)
                connection.execute(
                    """
                    INSERT INTO daily_summary(
                        day, steps, resting_heart_rate, average_stress,
                        body_battery_low, body_battery_high, sleep_seconds,
                        sleep_score, hrv_overnight, training_readiness, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day) DO UPDATE SET
                        steps=COALESCE(excluded.steps, daily_summary.steps),
                        resting_heart_rate=COALESCE(excluded.resting_heart_rate, daily_summary.resting_heart_rate),
                        average_stress=COALESCE(excluded.average_stress, daily_summary.average_stress),
                        body_battery_low=COALESCE(excluded.body_battery_low, daily_summary.body_battery_low),
                        body_battery_high=COALESCE(excluded.body_battery_high, daily_summary.body_battery_high),
                        sleep_seconds=COALESCE(excluded.sleep_seconds, daily_summary.sleep_seconds),
                        sleep_score=COALESCE(excluded.sleep_score, daily_summary.sleep_score),
                        hrv_overnight=COALESCE(excluded.hrv_overnight, daily_summary.hrv_overnight),
                        training_readiness=COALESCE(excluded.training_readiness, daily_summary.training_readiness),
                        updated_at=excluded.updated_at
                    """,
                    (
                        day,
                        fields.get("Steps"),
                        fields.get("Resting HR"),
                        fields.get("Stress (average)"),
                        fields.get("Body battery low"),
                        fields.get("Body battery high"),
                        fields.get("Sleep"),
                        fields.get("Sleep score"),
                        fields.get("HRV overnight"),
                        fields.get("Training readiness"),
                        utc_now(),
                    ),
                )
                daily_rows += 1
        # Keep every legacy note and original FIT archive in the same database.
        for folder in (source / "daily", source / "activities", source / "activity-history" / "original"):
            if not folder.exists():
                continue
            for path in folder.rglob("*"):
                if path.is_file():
                    logical_path = f"legacy/{path.relative_to(source).as_posix()}"
                    store_imported_file(connection, logical_path, "legacy-garmin-folder", path.read_bytes())
    print(
        f"Legacy import complete: {daily_rows} daily summaries, {imported} activities, "
        f"and {details} detail payloads. Database: {db_path}"
    )


def status(db_path: Path) -> None:
    if not db_path.exists():
        print(f"No database exists yet: {db_path}")
        return
    with database(db_path) as connection:
        counts = {
            "raw records": connection.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "daily summaries": connection.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0],
            "activities": connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0],
            "activity detail records": connection.execute("SELECT COUNT(*) FROM activity_records").fetchone()[0],
            "imported files": connection.execute("SELECT COUNT(*) FROM imported_files").fetchone()[0],
            "recorded sync errors": connection.execute("SELECT COUNT(*) FROM sync_errors").fetchone()[0],
        }
        print(f"Database: {db_path.resolve()}")
        print(f"Size: {db_path.stat().st_size:,} bytes")
        for label, value in counts.items():
            print(f"{label}: {value:,}")
        span = connection.execute("SELECT MIN(day), MAX(day) FROM daily_summary").fetchone()
        if span[0]:
            print(f"daily range: {span[0]} through {span[1]}")
        activity_span = connection.execute("SELECT MIN(start_time), MAX(start_time) FROM activities").fetchone()
        if activity_span[0]:
            print(f"activity range: {activity_span[0]} through {activity_span[1]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive Garmin Connect data in one local SQLite database.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="incrementally retrieve Garmin Connect data")
    sync_parser.add_argument("--start", type=parse_day, help="required on first sync; earliest day to retrieve")
    sync_parser.add_argument("--end", type=parse_day, default=date.today(), help="last day to retrieve")
    sync_parser.add_argument(
        "--refresh-days", type=int, default=2, help="re-fetch this many recent cursor days (default: 2)"
    )
    sync_parser.add_argument("--refresh-activities", action="store_true", help="re-fetch existing activity details")
    sync_parser.add_argument("--skip-daily", action="store_true", help="skip daily body/wellness endpoints")
    sync_parser.add_argument("--skip-activities", action="store_true", help="skip activities")

    import_parser = subparsers.add_parser(
        "import-export", help="store a Garmin account export ZIP/directory inside SQLite"
    )
    import_parser.add_argument("path", type=Path)

    legacy_parser = subparsers.add_parser("import-legacy", help="import this project's existing Garmin archive")
    legacy_parser.add_argument("--source", type=Path, default=DEFAULT_LEGACY_DIR)

    subparsers.add_parser("status", help="show local archive coverage")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db_path = args.database.resolve()
    if args.command == "sync":
        run_sync(
            db_path,
            args.start,
            args.end,
            max(0, args.refresh_days),
            args.refresh_activities,
            args.skip_daily,
            args.skip_activities,
        )
    elif args.command == "import-export":
        import_export(db_path, args.path)
    elif args.command == "import-legacy":
        import_legacy(db_path, args.source.resolve())
    elif args.command == "status":
        status(db_path)


if __name__ == "__main__":
    main()
