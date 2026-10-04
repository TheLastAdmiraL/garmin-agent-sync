"""Orchestration for scheduled Garmin-to-Google-Sheets synchronization."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from cloud_sync import (
    ACTIVITY_HEADER,
    DAILY_HEADER,
    FILE_HEADER,
    RAW_HEADER,
    IST,
    Collection,
    GarminCollector,
    RawRecord,
    activity_day,
    activity_row,
    daily_row,
    date_range,
    iso_ist,
    month_key,
    original_activity_rows,
    raw_rows,
)
from sheets_store import GoogleSheetsStore
from redaction import safe_error


def parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be YYYY-MM-DD") from error


def coalesce_ranges(days: set[date]) -> list[tuple[date, date]]:
    if not days:
        return []
    ordered = sorted(days)
    ranges: list[tuple[date, date]] = []
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue
        ranges.append((start, previous))
        start = previous = current
    ranges.append((start, previous))
    return ranges


def plan_sync_ranges(
    backfill_start: str,
    backfill_batch_days: int,
    refresh_days: int,
    today: date,
    state: dict[str, str],
    retry_days: list[str],
    manual_start: date | None = None,
    manual_end: date | None = None,
) -> tuple[list[tuple[date, date]], date, bool]:
    """Calculate the requested date windows without contacting Garmin or writing data."""
    configured_start = parse_date(backfill_start, "GARMIN_BACKFILL_START")
    next_backfill = parse_date(state.get("backfill_next_date", backfill_start), "backfill_next_date")
    recent_start = today - timedelta(days=max(0, refresh_days - 1))
    requested_days = set(date_range(recent_start, today))
    backfill_limit = recent_start - timedelta(days=1)

    if (manual_start is None) != (manual_end is None):
        raise ValueError("manual sync requires both start and end dates")
    if manual_start is not None and manual_end is not None:
        if manual_start > manual_end:
            raise ValueError("manual sync start date cannot be after end date")
        if manual_end > today:
            raise ValueError("manual sync end date cannot be in the future")
        requested_days.update(date_range(manual_start, manual_end))

    backfill_range: tuple[date, date] | None = None
    if next_backfill <= backfill_limit:
        batch_end = min(
            next_backfill + timedelta(days=max(1, backfill_batch_days) - 1),
            backfill_limit,
        )
        backfill_range = (next_backfill, batch_end)
        requested_days.update(date_range(next_backfill, batch_end))

    for retry_day in retry_days:
        try:
            parsed = date.fromisoformat(retry_day)
        except ValueError:
            continue
        if configured_start <= parsed <= today:
            requested_days.add(parsed)

    next_value = backfill_range[1] + timedelta(days=1) if backfill_range else next_backfill
    return coalesce_ranges(requested_days), next_value, next_value > backfill_limit


def tab_name(month: str, kind: str) -> str:
    return f"{month} {kind}"


def _run_sync_impl(
    spreadsheet_id: str,
    token_json: str,
    backfill_start: str,
    backfill_batch_days: int,
    refresh_days: int,
    trigger: str = "scheduler",
    today: date | None = None,
    store: GoogleSheetsStore | None = None,
    collector_factory: Any = GarminCollector,
    token_persister: Any | None = None,
    run_id: str = "",
    started_at: str = "",
    manual_start: date | None = None,
    manual_end: date | None = None,
) -> dict[str, Any]:
    """Run one resumable sync and return non-sensitive execution metadata."""

    local_today = today or datetime.now(IST).date()
    if store is None:
        raise ValueError("store is required for _run_sync_impl")
    store.ensure_tabs(["README", "Sync State", "Sync Log", "Errors", "Changes"])
    store.write_readme("RUNNING", started_at, "", "Sync is in progress", "")

    state = store.read_state()
    ranges, next_value, backfill_complete = plan_sync_ranges(
        backfill_start,
        backfill_batch_days,
        refresh_days,
        local_today,
        state,
        store.open_retry_days(limit=5),
        manual_start,
        manual_end,
    )
    requested_days = {day for start, end in ranges for day in date_range(start, end)}
    existing_original_activity_ids: set[str] = set()
    for month in {month_key(day.isoformat()) for day in requested_days}:
        for record_id in store.existing_record_ids(tab_name(month, "Files")):
            if record_id.startswith("activity_file|original_zip|"):
                existing_original_activity_ids.add(record_id.rsplit("|", 1)[-1])
    collector = collector_factory(token_json)
    collection = Collection()
    collection.extend(collector.collect_snapshots(local_today))
    collection.extend(collector.collect_daily(requested_days))
    collection.extend(
        collector.collect_activities(
            ranges,
            existing_original_activity_ids=existing_original_activity_ids,
        )
    )

    token_updated = False
    if token_persister is not None:
        error_key = "credential|secret_manager|GARMIN_TOKEN"
        try:
            token_updated = bool(token_persister(token_json, collector.refreshed_token()))
            collection.successful_keys.add(error_key)
        except Exception as error:
            from cloud_sync import SyncFailure

            collection.failures.append(
                SyncFailure(
                    "credential",
                    "secret_manager",
                    "GARMIN_TOKEN",
                    local_today.isoformat(),
                    type(error).__name__,
                    safe_error(error),
                )
            )
            collection.requests_failed += 1

    records_written = 0
    records_changed = 0
    changes: list[list[Any]] = []

    activities_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unique_activities: dict[str, dict[str, Any]] = {}
    for activity in collection.activities:
        activity_id = str(activity.get("activityId") or "")
        if activity_id:
            unique_activities[activity_id] = activity
        observed_on = activity_day(activity)
        if observed_on:
            activities_by_day[observed_on].append(activity)

    daily_records_by_day: dict[str, list[RawRecord]] = defaultdict(list)
    raw_by_month: dict[str, list[RawRecord]] = defaultdict(list)
    activity_raw_by_month: dict[str, list[RawRecord]] = defaultdict(list)
    for record in collection.raw_records:
        if record.category == "daily":
            daily_records_by_day[record.observed_on].append(record)
        if record.category == "activity":
            activity_raw_by_month[month_key(record.observed_on)].append(record)
        else:
            raw_by_month[month_key(record.observed_on)].append(record)

    for month, records in raw_by_month.items():
        incoming = [row for record in records for row in raw_rows(record)]
        written, changed, month_changes = store.replace_chunked_records(
            tab_name(month, "Raw"),
            RAW_HEADER,
            incoming,
            {record.record_id for record in records},
            "Lossless Garmin daily and snapshot JSON. Join chunks by record_id and chunk_index.",
        )
        records_written += written
        records_changed += changed
        changes.extend(month_changes)

    for month, records in activity_raw_by_month.items():
        incoming = [row for record in records for row in raw_rows(record)]
        written, changed, month_changes = store.replace_chunked_records(
            tab_name(month, "Activity Raw"),
            RAW_HEADER,
            incoming,
            {record.record_id for record in records},
            "Lossless Garmin activity endpoint JSON, including samples, splits, zones, weather, gear and sets.",
        )
        records_written += written
        records_changed += changed
        changes.extend(month_changes)

    originals_by_month: dict[str, list[Any]] = defaultdict(list)
    for original in collection.original_activities:
        originals_by_month[month_key(original.observed_on)].append(original)
    for month, records in originals_by_month.items():
        incoming = [row for record in records for row in original_activity_rows(record)]
        written, changed, month_changes = store.insert_new_chunked_records(
            tab_name(month, "Files"),
            FILE_HEADER,
            incoming,
            "Exact Garmin ORIGINAL activity ZIP bytes as base64 chunks; concatenate and decode to reconstruct.",
            sha_column=6,
        )
        records_written += written
        records_changed += changed
        changes.extend(month_changes)

    for month in sorted({month_key(day.isoformat()) for day in requested_days}):
        days = sorted(day.isoformat() for day in requested_days if month_key(day.isoformat()) == month)
        incoming = [
            daily_row(day, daily_records_by_day.get(day, []), activities_by_day.get(day, []))
            for day in days
        ]
        written, changed = store.upsert_rows(
            tab_name(month, "Daily"),
            DAILY_HEADER,
            incoming,
            key_column=0,
            sort_columns=(0,),
            description="Agent-ready daily health summary. Hourly HR includes actual sample counts and workout labels.",
        )
        records_written += written
        records_changed += changed

    activities_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in unique_activities.values():
        observed_on = activity_day(activity)
        if observed_on:
            activities_by_month[month_key(observed_on)].append(activity)
    for month, activities in activities_by_month.items():
        written, changed = store.upsert_rows(
            tab_name(month, "Activities"),
            ACTIVITY_HEADER,
            [activity_row(activity) for activity in activities],
            key_column=2,
            sort_columns=(0,),
            description="Agent-ready activity summaries. Full fields live in Activity Raw and original FIT/ZIP bytes in Files.",
        )
        records_written += written
        records_changed += changed

    store.append_changes(changes)
    store.update_errors(collection.failures, collection.successful_keys)

    store.write_state(
        {
            "backfill_start_date": (configured_start.isoformat(), "Configured first Garmin day"),
            "backfill_next_date": (next_value.isoformat(), "Next historical day to fetch"),
            "backfill_complete": (str(backfill_complete).upper(), "TRUE once history has caught the rolling refresh window"),
            "last_run_id": (run_id, "Latest execution identifier"),
            "last_run_started_at_ist": (started_at, ""),
            "last_requested_ranges": (
                json.dumps([[start.isoformat(), end.isoformat()] for start, end in ranges]),
                "Historical, correction-window and retry ranges",
            ),
        }
    )

    completed_at = iso_ist()
    status = "SUCCESS" if not collection.failures else "PARTIAL"
    success_summary = (
        f"{collection.requests_ok} Garmin calls succeeded; {records_written} sheet rows written; "
        f"{records_changed} source records or table rows changed"
    )
    failure_summary = (
        "None"
        if not collection.failures
        else f"{collection.requests_failed} calls failed or were unsupported; inspect Errors"
    )
    store.append_sync_log(
        [
            started_at,
            completed_at,
            run_id,
            trigger,
            status,
            json.dumps([[start.isoformat(), end.isoformat()] for start, end in ranges]),
            collection.requests_ok,
            collection.requests_failed,
            records_written,
            records_changed,
            next_value.isoformat(),
            f"backfill_complete={backfill_complete}",
        ]
    )
    # Deliberately last: agents can use this as the commit marker for the run.
    store.write_readme(status, started_at, completed_at, success_summary, failure_summary)

    return {
        "run_id": run_id,
        "status": status,
        "started_at_ist": started_at,
        "completed_at_ist": completed_at,
        "requests_ok": collection.requests_ok,
        "requests_failed": collection.requests_failed,
        "records_written": records_written,
        "records_changed": records_changed,
        "backfill_next_date": next_value.isoformat(),
        "backfill_complete": backfill_complete,
        "token_updated": token_updated,
    }


def run_sync(
    spreadsheet_id: str,
    token_json: str,
    backfill_start: str,
    backfill_batch_days: int,
    refresh_days: int,
    trigger: str = "scheduler",
    today: date | None = None,
    store: GoogleSheetsStore | None = None,
    collector_factory: Any = GarminCollector,
    token_persister: Any | None = None,
    manual_start: date | None = None,
    manual_end: date | None = None,
) -> dict[str, Any]:
    """Run a sync and always replace the RUNNING marker on terminal failure."""
    actual_store = store or GoogleSheetsStore(spreadsheet_id)
    started_at = iso_ist()
    run_id = str(uuid.uuid4())
    try:
        return _run_sync_impl(
            spreadsheet_id=spreadsheet_id,
            token_json=token_json,
            backfill_start=backfill_start,
            backfill_batch_days=backfill_batch_days,
            refresh_days=refresh_days,
            trigger=trigger,
            today=today,
            store=actual_store,
            collector_factory=collector_factory,
            token_persister=token_persister,
            run_id=run_id,
            started_at=started_at,
            manual_start=manual_start,
            manual_end=manual_end,
        )
    except Exception as error:
        completed_at = iso_ist()
        message = f"{type(error).__name__}: {safe_error(error, 500)}"
        try:
            actual_store.append_sync_log(
                [started_at, completed_at, run_id, trigger, "FAILED", "", 0, 1, 0, 0, "", message]
            )
            actual_store.write_readme("FAILED", started_at, completed_at, "None", message)
        except Exception:
            pass
        raise
