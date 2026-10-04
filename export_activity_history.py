#!/usr/bin/env python3
"""Export all Garmin activity data to local, ignored files.

This is deliberately read-only: it retrieves data from Garmin and makes no
changes to the Garmin account. The export can include GPS and other sensitive
activity details, so the output remains under the git-ignored ``garmin/`` dir.
It is resumable: existing successful activity files are left intact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

from token_store import load as load_token
from token_store import save as save_token
from functions.redaction import safe_error


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "garmin" / "activity-history"
PAGE_SIZE = 100
REQUEST_DELAY_SECONDS = 0.4
RETRIES = 3


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8"))


def authenticated_client() -> Garmin:
    try:
        client = Garmin()
        client.login(load_token())
        return client
    except Exception as error:
        sys.exit(f"Could not use the saved Garmin login ({type(error).__name__}). Run sync_garmin.py --login again.")


def retrieve(label: str, method: Callable[..., Any], *args: Any) -> tuple[Any | None, str | None]:
    """Retrieve one endpoint with modest retries; preserve a per-endpoint error."""
    for attempt in range(RETRIES):
        try:
            value = method(*args)
            time.sleep(REQUEST_DELAY_SECONDS)
            return value, None
        except Exception as error:  # An endpoint may not apply to an activity.
            if attempt == RETRIES - 1:
                return None, f"{type(error).__name__}: {safe_error(error)}"
            time.sleep(2**attempt)
    return None, f"{label}: retrieval failed"


def all_activities(client: Garmin) -> list[dict[str, Any]]:
    total = client.count_activities()
    print(f"Garmin reports {total} activities.", flush=True)
    activities: list[dict[str, Any]] = []
    for start in range(0, total, PAGE_SIZE):
        page, error = retrieve("activity list", client.get_activities, start, min(PAGE_SIZE, total - start))
        if error:
            sys.exit(f"Unable to list activities at offset {start}: {error}")
        if not isinstance(page, list):
            sys.exit(f"Unexpected activity-list response at offset {start}.")
        activities.extend(item for item in page if isinstance(item, dict))
        print(f"Listed {len(activities)}/{total} activities.", flush=True)
        if not page:
            break
    return activities


def activity_payload(client: Garmin, activity_id: str) -> dict[str, Any]:
    endpoints: dict[str, Callable[[str], Any]] = {
        "summary": client.get_activity,
        "details": client.get_activity_details,
        "heart_rate_time_in_zones": client.get_activity_hr_in_timezones,
        "power_time_in_zones": client.get_activity_power_in_timezones,
        "splits": client.get_activity_splits,
        "typed_splits": client.get_activity_typed_splits,
        "weather": client.get_activity_weather,
        "gear": client.get_activity_gear,
        "exercise_sets": client.get_activity_exercise_sets,
    }
    payload: dict[str, Any] = {"activity_id": activity_id, "data": {}, "unavailable": {}}
    for name, method in endpoints.items():
        value, error = retrieve(name, method, activity_id)
        if error:
            payload["unavailable"][name] = error
        else:
            payload["data"][name] = value
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only, resumable Garmin activity-history export.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="local output folder")
    parser.add_argument("--include-original", action="store_true", help="also download each original activity archive")
    parser.add_argument("--refresh", action="store_true", help="re-download activity JSON already saved")
    args = parser.parse_args()
    output = args.out.resolve()
    activity_dir = output / "activities"
    original_dir = output / "original"
    client = authenticated_client()
    activities = all_activities(client)
    atomic_write_json(output / "activity-index.json", activities)

    completed = skipped = 0
    for index, activity in enumerate(activities, start=1):
        activity_id = activity.get("activityId")
        if not activity_id:
            print(f"Skipping list entry {index}: missing activity ID.", flush=True)
            continue
        activity_id = str(activity_id)
        detail_path = activity_dir / f"{activity_id}.json"
        if detail_path.exists() and not args.refresh:
            skipped += 1
        else:
            payload = activity_payload(client, activity_id)
            atomic_write_json(detail_path, payload)
            completed += 1
        if args.include_original:
            archive_path = original_dir / f"{activity_id}.zip"
            if not archive_path.exists() or args.refresh:
                archive, error = retrieve(
                    "original activity archive", client.download_activity, activity_id, Garmin.ActivityDownloadFormat.ORIGINAL
                )
                if error:
                    print(f"Original activity archive unavailable ({error}).", flush=True)
                elif isinstance(archive, bytes):
                    atomic_write_bytes(archive_path, archive)
        print(f"Processed {index}/{len(activities)} activities.", flush=True)

    save_token(client.client.dumps())
    print(f"Export complete: {completed} downloaded, {skipped} resumed. Output: {output}")


if __name__ == "__main__":
    main()
