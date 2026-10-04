#!/usr/bin/env python3
"""Read selected Garmin Connect data into local Markdown notes.

This program has no Garmin write operations and no cloud or database sink.
Passwords are requested only during --login and are never printed or saved.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from token_store import load as load_token
from token_store import save as save_token
from token_store import token_path

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("The Garmin library is not installed. Follow SETUP.md first.")


# A month gives useful trend context while keeping on-demand pulls modest enough
# to avoid repeatedly polling Garmin's unofficial endpoints.
MAX_DAYS = 31
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "garmin"


def clean_text(value: Any, limit: int = 80) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    return cleaned[:limit]


def safe_filename(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "unknown"))[:48]
    return cleaned or "unknown"


def value_at(data: Any, *keys: str | int) -> Any:
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and isinstance(key, int) and 0 <= key < len(data):
            data = data[key]
        else:
            return None
    return data


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def login() -> None:
    if not sys.stdin.isatty():
        sys.exit("Run --login in PowerShell or Windows Terminal so the password can stay hidden.")
    email = input("Garmin email: ").strip()
    if not email:
        sys.exit("No email entered.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Garmin password (hidden): ")
        except getpass.GetPassWarning:
            sys.exit("This terminal cannot hide the password. Use Windows Terminal or PowerShell.")
    try:
        client = Garmin(email=email, password=password, prompt_mfa=lambda: input("Garmin 2FA code: ").strip())
        client.login()
        save_token(client.client.dumps())
    except Exception as error:
        sys.exit(f"Login failed ({type(error).__name__}). Check your credentials or 2FA code and try again.")
    print(f"Login succeeded. Encrypted token saved outside this project at {token_path().parent}.")


def authenticated_client() -> Garmin:
    try:
        client = Garmin()
        client.login(load_token())
        return client
    except Exception as error:
        sys.exit(f"Could not use the saved Garmin login ({type(error).__name__}). Run --login again.")


def fetch(client: Garmin, method_name: str, day: str) -> Any:
    try:
        return getattr(client, method_name)(day)
    except Exception as error:
        print(f"Skipped {method_name} for {day} ({type(error).__name__}).", file=sys.stderr)
        return None


def render_daily(day: str, stats: Any, sleep: Any, hrv: Any, readiness: Any) -> str:
    fields = [
        ("Resting HR", value_at(stats, "restingHeartRate"), " bpm"),
        ("Steps", value_at(stats, "totalSteps"), ""),
        ("Stress (average)", value_at(stats, "averageStressLevel"), ""),
        ("Body battery low", value_at(stats, "bodyBatteryLowestValue"), ""),
        ("Body battery high", value_at(stats, "bodyBatteryHighestValue"), ""),
        ("Sleep", value_at(sleep, "dailySleepDTO", "sleepTimeSeconds"), " seconds"),
        ("Sleep score", value_at(sleep, "dailySleepDTO", "sleepScores", "overall", "value"), ""),
        ("HRV overnight", value_at(hrv, "hrvSummary", "lastNightAvg"), " ms"),
        ("Training readiness", value_at(readiness, 0, "score"), ""),
    ]
    lines = [f"# Garmin wellness — {day}", ""]
    for label, value, suffix in fields:
        display = "n/a" if value is None else f"{value}{suffix}"
        lines.append(f"- {label}: {display}")
    return "\n".join(lines) + "\n"


def render_activity(activity: dict[str, Any]) -> tuple[str, str]:
    activity_id = str(activity.get("activityId") or "unknown")
    start = clean_text(activity.get("startTimeLocal") or activity.get("startTimeGMT")) or "unknown"
    activity_type = clean_text(value_at(activity, "activityType", "typeKey")) or "unknown"
    name = clean_text(activity.get("activityName")) or "Activity"
    day = start[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", start) else "unknown-date"
    lines = [
        f"# Garmin activity — {day}",
        "",
        f"- Name: {name}",
        f"- Type: {activity_type}",
        f"- Start: {start}",
        f"- Duration: {activity.get('duration', 'n/a')} seconds",
        f"- Distance: {activity.get('distance', 'n/a')} metres",
        f"- Average HR: {activity.get('averageHR', 'n/a')} bpm",
        f"- Elevation gain: {activity.get('elevationGain', 'n/a')} metres",
    ]
    filename = f"{day}-{safe_filename(activity_type)}-{safe_filename(activity_id)}.md"
    return filename, "\n".join(lines) + "\n"


def sync(days: int, output: Path, dry_run: bool) -> None:
    client = authenticated_client()
    today = date.today()
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        daily = render_daily(
            day,
            fetch(client, "get_stats", day),
            fetch(client, "get_sleep_data", day),
            fetch(client, "get_hrv_data", day),
            fetch(client, "get_training_readiness", day),
        )
        if not dry_run:
            atomic_write(output / "daily" / f"{day}.md", daily)
    start = (today - timedelta(days=days - 1)).isoformat()
    try:
        activities = client.get_activities_by_date(start, today.isoformat()) or []
    except Exception as error:
        print(f"Skipped activities ({type(error).__name__}).", file=sys.stderr)
        activities = []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        filename, note = render_activity(activity)
        if not dry_run:
            atomic_write(output / "activities" / filename, note)
    # Persist a refreshed session token, still encrypted with DPAPI.
    save_token(client.client.dumps())
    if dry_run:
        print(f"Dry run complete; {days} daily records and {len(activities)} activity notes would be written. No record values were printed or saved.")
    else:
        print(f"Sync complete: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local, retrieval-only Garmin Connect sync.")
    parser.add_argument("--login", action="store_true", help="one-time local Garmin login")
    parser.add_argument("--days", type=int, default=3, help="days to retrieve (1-31)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="local Markdown output directory")
    parser.add_argument("--dry-run", action="store_true", help="retrieve but do not write health files")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        sys.exit("Python 3.12 or later is required. See SETUP.md.")
    if args.login:
        login()
        return
    sync(max(1, min(args.days, MAX_DAYS)), args.out.resolve(), args.dry_run)


if __name__ == "__main__":
    main()
