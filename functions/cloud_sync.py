"""Garmin Connect collection and lossless Google Sheets row transforms.

The collector is read-only with respect to Garmin.  Every JSON response is
preserved in chunked rows, while compact daily/activity rows make the workbook
easy for an AI agent to understand without first parsing the raw payloads.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from garminconnect import Garmin
from redaction import safe_error


IST = ZoneInfo("Asia/Kolkata")
MAX_CELL_CHARS = 40_000
REQUEST_DELAY_SECONDS = 0.45
RETRIES = 3


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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_ist(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(IST).isoformat(timespec="seconds")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def chunks(value: str, size: int = MAX_CELL_CHARS) -> list[str]:
    if not value:
        return [""]
    return [value[index : index + size] for index in range(0, len(value), size)]


def month_key(day: str) -> str:
    return day[:7]


def date_range(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


@dataclass(frozen=True)
class RawRecord:
    category: str
    dataset: str
    record_key: str
    observed_on: str
    payload: Any
    fetched_at_ist: str

    @property
    def record_id(self) -> str:
        return f"{self.category}|{self.dataset}|{self.record_key}"

    @property
    def payload_json(self) -> str:
        return json_text(self.payload)

    @property
    def digest(self) -> str:
        return sha256(self.payload_json)


@dataclass(frozen=True)
class OriginalActivity:
    activity_id: str
    observed_on: str
    payload: bytes
    fetched_at_ist: str

    @property
    def record_id(self) -> str:
        return f"activity_file|original_zip|{self.activity_id}"

    @property
    def digest(self) -> str:
        return sha256(self.payload)


@dataclass(frozen=True)
class SyncFailure:
    category: str
    dataset: str
    record_key: str
    observed_on: str
    error_type: str
    message: str
    retryable: bool = True

    @property
    def error_key(self) -> str:
        return f"{self.category}|{self.dataset}|{self.record_key}"


@dataclass
class Collection:
    raw_records: list[RawRecord] = field(default_factory=list)
    activities: list[dict[str, Any]] = field(default_factory=list)
    original_activities: list[OriginalActivity] = field(default_factory=list)
    failures: list[SyncFailure] = field(default_factory=list)
    successful_keys: set[str] = field(default_factory=set)
    requests_ok: int = 0
    requests_failed: int = 0

    def extend(self, other: "Collection") -> None:
        self.raw_records.extend(other.raw_records)
        self.activities.extend(other.activities)
        self.original_activities.extend(other.original_activities)
        self.failures.extend(other.failures)
        self.successful_keys.update(other.successful_keys)
        self.requests_ok += other.requests_ok
        self.requests_failed += other.requests_failed


class GarminCollector:
    """Retrieve Garmin data with bounded retries and no Garmin write calls."""

    def __init__(self, token_json: str, request_delay: float = REQUEST_DELAY_SECONDS) -> None:
        self.client = Garmin()
        self.client.login(token_json)
        self.request_delay = request_delay

    def refreshed_token(self) -> str:
        return self.client.client.dumps()

    def _call(self, method_name: str, *args: Any) -> Any:
        method = getattr(self.client, method_name)
        last_error: Exception | None = None
        for attempt in range(RETRIES):
            try:
                result = method(*args)
                time.sleep(self.request_delay)
                return result
            except Exception as error:  # The library exposes several transport-specific errors.
                last_error = error
                if attempt + 1 < RETRIES:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    def collect_daily(self, days: Iterable[date]) -> Collection:
        result = Collection()
        fetched_at = iso_ist()
        for current in sorted(set(days)):
            day = current.isoformat()
            for dataset, (method_name, style) in DAILY_ENDPOINTS.items():
                error_key = f"daily|{dataset}|{day}"
                if not hasattr(self.client, method_name):
                    result.failures.append(
                        SyncFailure(
                            "daily",
                            dataset,
                            day,
                            day,
                            "UnsupportedMethod",
                            f"garminconnect has no {method_name} method",
                            retryable=False,
                        )
                    )
                    result.requests_failed += 1
                    continue
                try:
                    args = (day, day) if style == "same_day_range" else (day,)
                    payload = self._call(method_name, *args)
                    result.raw_records.append(RawRecord("daily", dataset, day, day, payload, fetched_at))
                    result.successful_keys.add(error_key)
                    result.requests_ok += 1
                except Exception as error:
                    result.failures.append(
                        SyncFailure("daily", dataset, day, day, type(error).__name__, safe_error(error))
                    )
                    result.requests_failed += 1
        return result

    def collect_snapshots(self, observed_on: date) -> Collection:
        result = Collection()
        day = observed_on.isoformat()
        fetched_at = iso_ist()
        for dataset, (method_name, args) in SNAPSHOT_ENDPOINTS.items():
            error_key = f"snapshot|{dataset}|{day}"
            if not hasattr(self.client, method_name):
                result.failures.append(
                    SyncFailure(
                        "snapshot",
                        dataset,
                        day,
                        day,
                        "UnsupportedMethod",
                        f"garminconnect has no {method_name} method",
                        retryable=False,
                    )
                )
                result.requests_failed += 1
                continue
            try:
                payload = self._call(method_name, *args)
                result.raw_records.append(RawRecord("snapshot", dataset, day, day, payload, fetched_at))
                result.successful_keys.add(error_key)
                result.requests_ok += 1
            except Exception as error:
                result.failures.append(
                    SyncFailure("snapshot", dataset, day, day, type(error).__name__, safe_error(error))
                )
                result.requests_failed += 1
        return result

    def collect_activities(
        self,
        ranges: Iterable[tuple[date, date]],
        existing_original_activity_ids: set[str] | None = None,
    ) -> Collection:
        result = Collection()
        existing_original_activity_ids = existing_original_activity_ids or set()
        activity_by_id: dict[str, dict[str, Any]] = {}
        for start, end in ranges:
            if start > end:
                continue
            try:
                payload = self._call("get_activities_by_date", start.isoformat(), end.isoformat()) or []
                result.requests_ok += 1
                for activity in payload:
                    if isinstance(activity, dict) and activity.get("activityId") is not None:
                        activity_by_id[str(activity["activityId"])] = activity
            except Exception as error:
                key = f"{start.isoformat()}:{end.isoformat()}"
                result.failures.append(
                    SyncFailure(
                        "activity_index",
                        "activities_by_date",
                        key,
                        start.isoformat(),
                        type(error).__name__,
                        safe_error(error),
                    )
                )
                result.requests_failed += 1

        for activity_id, activity in activity_by_id.items():
            observed_on = activity_day(activity) or date.today().isoformat()
            result.activities.append(activity)
            fetched_at = iso_ist()
            for dataset, method_name in ACTIVITY_ENDPOINTS.items():
                error_key = f"activity|{dataset}|{activity_id}"
                if not hasattr(self.client, method_name):
                    result.failures.append(
                        SyncFailure(
                            "activity",
                            dataset,
                            activity_id,
                            observed_on,
                            "UnsupportedMethod",
                            f"garminconnect has no {method_name} method",
                            retryable=False,
                        )
                    )
                    result.requests_failed += 1
                    continue
                try:
                    payload = self._call(method_name, activity_id)
                    result.raw_records.append(
                        RawRecord("activity", dataset, activity_id, observed_on, payload, fetched_at)
                    )
                    result.successful_keys.add(error_key)
                    result.requests_ok += 1
                except Exception as error:
                    result.failures.append(
                        SyncFailure(
                            "activity",
                            dataset,
                            activity_id,
                            observed_on,
                            type(error).__name__,
                            safe_error(error),
                        )
                    )
                    result.requests_failed += 1

            error_key = f"activity_file|original_zip|{activity_id}"
            if activity_id in existing_original_activity_ids:
                result.successful_keys.add(error_key)
                continue
            try:
                payload = self._call(
                    "download_activity",
                    activity_id,
                    Garmin.ActivityDownloadFormat.ORIGINAL,
                )
                if not isinstance(payload, bytes):
                    raise TypeError("Garmin returned non-binary original activity data")
                result.original_activities.append(
                    OriginalActivity(activity_id, observed_on, payload, fetched_at)
                )
                result.successful_keys.add(error_key)
                result.requests_ok += 1
            except Exception as error:
                result.failures.append(
                    SyncFailure(
                        "activity_file",
                        "original_zip",
                        activity_id,
                        observed_on,
                        type(error).__name__,
                        safe_error(error),
                    )
                )
                result.requests_failed += 1
        return result


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


def number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def value_for(payloads: dict[str, Any], dataset: str, *names: str) -> Any:
    return recursively_find(payloads.get(dataset), names)


def numeric_for(payloads: dict[str, Any], dataset: str, *names: str) -> float | None:
    return number(value_for(payloads, dataset, *names))


def parse_datetime(value: Any, assume_local: bool = True) -> datetime | None:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, timezone.utc).astimezone(IST)
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST if assume_local else timezone.utc)
    return parsed.astimezone(IST)


def activity_day(activity: dict[str, Any]) -> str | None:
    parsed = parse_datetime(activity.get("startTimeLocal") or activity.get("startTimeGMT"))
    return parsed.date().isoformat() if parsed else None


def activity_type(activity: dict[str, Any]) -> str:
    value = activity.get("activityType")
    if isinstance(value, dict):
        value = value.get("typeKey") or value.get("parentTypeId") or value.get("typeId")
    return str(value or "unknown")


def activity_interval(activity: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start = parse_datetime(activity.get("startTimeLocal") or activity.get("startTimeGMT"))
    duration = number(activity.get("duration"))
    if start is None or duration is None:
        return None
    return start, start + timedelta(seconds=max(0, duration))


def _find_hr_lists(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"heartratevalues", "heart_rate_values"} and isinstance(item, list):
                found.extend(item)
            else:
                found.extend(_find_hr_lists(item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                found.extend(_find_hr_lists(item))
    return found


def heart_rate_samples(payload: Any) -> list[tuple[datetime, float]]:
    samples: list[tuple[datetime, float]] = []
    for item in _find_hr_lists(payload):
        timestamp: Any = None
        heart_rate: Any = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            timestamp, heart_rate = item[0], item[1]
        elif isinstance(item, dict):
            timestamp = item.get("timestamp") or item.get("timestampGMT") or item.get("time")
            heart_rate = item.get("heartRate") or item.get("value")
        parsed = parse_datetime(timestamp, assume_local=False)
        numeric = number(heart_rate)
        if parsed is not None and numeric is not None and numeric > 0:
            samples.append((parsed, numeric))
    return samples


def hourly_hr_text(day: str, heart_rate_payload: Any, activities: list[dict[str, Any]]) -> str:
    wanted_day = date.fromisoformat(day)
    values: dict[int, list[float]] = defaultdict(list)
    for timestamp, heart_rate in heart_rate_samples(heart_rate_payload):
        if timestamp.date() == wanted_day:
            values[timestamp.hour].append(heart_rate)

    intervals: list[tuple[datetime, datetime, str]] = []
    for activity in activities:
        interval = activity_interval(activity)
        if interval:
            intervals.append((*interval, activity_type(activity)))

    windows: list[str] = []
    for hour in range(24):
        start = datetime.combine(wanted_day, datetime_time(hour), tzinfo=IST)
        end = start + timedelta(hours=1)
        labels = sorted({kind for activity_start, activity_end, kind in intervals if activity_start < end and activity_end > start})
        activity_label = f" [ACTIVITY:{','.join(labels)}]" if labels else ""
        samples = values.get(hour, [])
        next_hour = (hour + 1) % 24
        if samples:
            average = sum(samples) / len(samples)
            windows.append(f"{hour:02d}-{next_hour:02d}: {average:.1f} bpm (n={len(samples)}){activity_label}")
        else:
            windows.append(f"{hour:02d}-{next_hour:02d}: NA (n=0){activity_label}")
    return "; ".join(windows)


def activity_intervals_text(activities: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for activity in sorted(activities, key=lambda item: str(item.get("startTimeLocal") or "")):
        interval = activity_interval(activity)
        if not interval:
            continue
        start, end = interval
        rendered.append(
            f"{start:%H:%M}-{end:%H:%M} {activity_type(activity)} "
            f"({activity.get('activityName') or 'unnamed'}, id={activity.get('activityId')})"
        )
    return "; ".join(rendered)


DAILY_HEADER = [
    "date",
    "updated_at_ist",
    "steps",
    "distance_m",
    "calories",
    "floors_ascended",
    "intensity_minutes",
    "resting_hr_bpm",
    "average_stress",
    "body_battery_low",
    "body_battery_high",
    "sleep_seconds",
    "sleep_hours",
    "sleep_score",
    "hrv_overnight_ms",
    "training_readiness",
    "training_status",
    "vo2_max",
    "average_spo2",
    "average_respiration",
    "hourly_hr_local_actual_samples",
    "activity_intervals_local",
    "activity_count",
    "activity_ids",
    "source_hashes_json",
]


def daily_row(day: str, records: list[RawRecord], activities: list[dict[str, Any]]) -> list[Any]:
    payloads = {record.dataset: record.payload for record in records if record.category == "daily"}
    sleep_seconds = numeric_for(payloads, "sleep", "sleepTimeSeconds", "sleepDuration")
    source_hashes = {record.dataset: record.digest for record in records if record.category == "daily"}
    ids = [str(activity.get("activityId")) for activity in activities if activity.get("activityId") is not None]
    return [
        day,
        iso_ist(),
        numeric_for(payloads, "stats", "totalSteps", "steps"),
        numeric_for(payloads, "stats", "totalDistanceMeters", "totalDistance", "distance"),
        numeric_for(payloads, "calories", "totalKilocalories", "activeKilocalories", "calories"),
        numeric_for(payloads, "floors", "floorsAscended", "floorsClimbed"),
        numeric_for(payloads, "intensity_minutes", "totalIntensityMinutes", "moderateIntensityMinutes"),
        numeric_for(payloads, "stats", "restingHeartRate", "restingHR"),
        numeric_for(payloads, "all_day_stress", "averageStressLevel", "avgStressLevel"),
        numeric_for(payloads, "stats", "bodyBatteryLowestValue", "bodyBatteryLow"),
        numeric_for(payloads, "stats", "bodyBatteryHighestValue", "bodyBatteryHigh"),
        sleep_seconds,
        round(sleep_seconds / 3600, 3) if sleep_seconds is not None else None,
        numeric_for(payloads, "sleep", "sleepScore", "value"),
        numeric_for(payloads, "hrv", "lastNightAvg", "lastNightAverage"),
        numeric_for(payloads, "training_readiness", "score", "trainingReadinessScore"),
        value_for(payloads, "training_status", "trainingStatus", "status"),
        numeric_for(payloads, "max_metrics", "vo2Max", "generic", "vo2MaxValue"),
        numeric_for(payloads, "spo2", "averageSpO2", "avgSpO2", "averageSpo2"),
        numeric_for(payloads, "respiration", "avgWakingRespirationValue", "averageRespirationValue"),
        hourly_hr_text(day, payloads.get("heart_rates"), activities),
        activity_intervals_text(activities),
        len(activities),
        ",".join(ids),
        json_text(source_hashes),
    ]


ACTIVITY_HEADER = [
    "start_time_local",
    "date",
    "activity_id",
    "activity_type",
    "name",
    "duration_seconds",
    "distance_m",
    "average_hr_bpm",
    "max_hr_bpm",
    "calories",
    "elevation_gain_m",
    "average_speed_mps",
    "average_cadence",
    "average_power_w",
    "aerobic_training_effect",
    "anaerobic_training_effect",
    "summary_sha256",
    "full_summary_json",
]


def activity_row(activity: dict[str, Any]) -> list[Any]:
    summary = json_text(activity)
    return [
        activity.get("startTimeLocal") or activity.get("startTimeGMT"),
        activity_day(activity),
        str(activity.get("activityId") or ""),
        activity_type(activity),
        activity.get("activityName"),
        number(activity.get("duration")),
        number(activity.get("distance")),
        number(activity.get("averageHR")),
        number(activity.get("maxHR")),
        number(activity.get("calories")),
        number(activity.get("elevationGain")),
        number(activity.get("averageSpeed")),
        number(activity.get("averageRunningCadenceInStepsPerMinute") or activity.get("averageCadence")),
        number(activity.get("avgPower") or activity.get("averagePower")),
        number(activity.get("aerobicTrainingEffect")),
        number(activity.get("anaerobicTrainingEffect")),
        sha256(summary),
        summary if len(summary) <= MAX_CELL_CHARS else "See the matching activity summary record in Activity Raw",
    ]


RAW_HEADER = [
    "unique_chunk_key",
    "record_id",
    "observed_on",
    "category",
    "dataset",
    "record_key",
    "fetched_at_ist",
    "sha256",
    "chunk_index",
    "chunk_count",
    "payload_json_chunk",
]


def raw_rows(record: RawRecord) -> list[list[Any]]:
    payload_chunks = chunks(record.payload_json)
    return [
        [
            f"{record.record_id}|{index:05d}",
            record.record_id,
            record.observed_on,
            record.category,
            record.dataset,
            record.record_key,
            record.fetched_at_ist,
            record.digest,
            index,
            len(payload_chunks),
            chunk,
        ]
        for index, chunk in enumerate(payload_chunks, start=1)
    ]
FILE_HEADER = [
    "unique_chunk_key",
    "record_id",
    "observed_on",
    "activity_id",
    "format",
    "fetched_at_ist",
    "sha256",
    "byte_length",
    "chunk_index",
    "chunk_count",
    "base64_chunk",
]


def original_activity_rows(record: OriginalActivity) -> list[list[Any]]:
    encoded = base64.b64encode(record.payload).decode("ascii")
    payload_chunks = chunks(encoded)
    return [
        [
            f"{record.record_id}|{index:05d}",
            record.record_id,
            record.observed_on,
            record.activity_id,
            "Garmin ORIGINAL ZIP (usually contains FIT)",
            record.fetched_at_ist,
            record.digest,
            len(record.payload),
            index,
            len(payload_chunks),
            chunk,
        ]
        for index, chunk in enumerate(payload_chunks, start=1)
    ]
