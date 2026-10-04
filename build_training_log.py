#!/usr/bin/env python3
"""Build the local master index for imported history and Garmin exports."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "garmin"
ACTIVITIES = ROOT / "activity-history" / "activities"


def format_duration(seconds: object) -> str:
    if not isinstance(seconds, (int, float)):
        return "-"
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def format_distance(metres: object) -> str:
    if not isinstance(metres, (int, float)):
        return "-"
    return f"{metres / 1000:.2f}"


def activity_rows() -> list[dict[str, object]]:
    rows = []
    for path in ACTIVITIES.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        summary = payload.get("data", {}).get("summary", {})
        metrics = summary.get("summaryDTO", {})
        rows.append(
            {
                "id": str(summary.get("activityId", path.stem)),
                "started": metrics.get("startTimeLocal", ""),
                "type": summary.get("activityTypeDTO", {}).get("typeKey", "unknown"),
                "name": str(summary.get("activityName") or "Activity").replace("|", "\\|"),
                "duration": metrics.get("duration"),
                "distance": metrics.get("distance", metrics.get("distanceMeter")),
                "average_hr": metrics.get("averageHR"),
                "max_hr": metrics.get("maxHR"),
            }
        )
    return sorted(rows, key=lambda item: str(item["started"]))


def main() -> None:
    rows = activity_rows()
    history_note = ROOT / "HISTORY.md"
    lines = [
        "# Master Training Log",
        "",
        "This generated file indexes local training notes and Garmin-synchronised records.",
        "",
        "## Scope and sources",
        "",
        "- [Daily Garmin wellness notes](daily/): sleep, resting HR, HRV, stress, Body Battery, steps, and readiness for the synced dates.",
        f"- [Garmin activity index](activity-history/activity-index.json): all {len(rows)} exported Garmin activities.",
        "- [Detailed Garmin activity records](activity-history/activities/): one JSON file per activity with summary, chart data, HR time series, HR/power zones, splits, weather, gear, and exercise sets.",
        "- [Original Garmin activity archives](activity-history/original/): original activity downloads, including sensitive location and biometric data.",
    ]
    if history_note.exists():
        lines.append("- [Local training notes](HISTORY.md): user-managed context stored only in this ignored local data directory.")
    lines += [
        "",
        "## Garmin-synchronised activity log",
        "",
        "| Date/time | Type | Session | Distance (km) | Duration | Avg HR | Max HR | Detailed record |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        date = str(row["started"]).replace("T", " ") or "-"
        activity_id = row["id"]
        lines.append(
            f"| {date} | {row['type']} | {row['name']} | {format_distance(row['distance'])} | "
            f"{format_duration(row['duration'])} | {row['average_hr'] or '-'} | {row['max_hr'] or '-'} | "
            f"[JSON](activity-history/activities/{activity_id}.json) |"
        )
    lines += [
        "",
        "## How to maintain this log",
        "",
        "Run `sync_garmin.py --days 31` to refresh recent wellness notes and Markdown activity summaries. Run `export_activity_history.py --include-original` to add new activity details and original archives. Then run `python build_training_log.py` to refresh this index.",
        "",
        "## Privacy",
        "",
        "This folder is git-ignored. Do not upload its contents or share the original activity archives unless you intend to share location, health, and biometric data.",
    ]
    (ROOT / "TRAINING_LOG.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {ROOT / 'TRAINING_LOG.md'} with {len(rows)} activities.")


if __name__ == "__main__":
    main()
