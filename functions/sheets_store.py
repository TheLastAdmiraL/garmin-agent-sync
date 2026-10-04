"""Google Sheets persistence for the Garmin cloud sync."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import re
import time
import uuid
from typing import Any

from google.auth import default as default_credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from cloud_sync import IST, SyncFailure, iso_ist


HEADER_ROW = 9
DATA_START_ROW = HEADER_ROW + 1

STATE_HEADER = ["key", "value", "updated_at_ist", "notes"]
SYNC_LOG_HEADER = [
    "started_at_ist",
    "completed_at_ist",
    "run_id",
    "trigger",
    "status",
    "date_ranges",
    "requests_ok",
    "requests_failed",
    "records_written",
    "records_changed",
    "backfill_next_date",
    "details",
]
ERROR_HEADER = [
    "error_key",
    "status",
    "category",
    "dataset",
    "record_key",
    "observed_on",
    "first_seen_ist",
    "last_attempt_ist",
    "attempts",
    "error_type",
    "message",
]
CHANGE_HEADER = [
    "change_id",
    "changed_at_ist",
    "record_id",
    "observed_on",
    "tab",
    "change_type",
    "old_sha256",
    "new_sha256",
]
SHEETS_RETRIES = 3


class GoogleSheetsStore:
    def __init__(self, spreadsheet_id: str, service: Any | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        if service is None:
            credentials, _ = default_credentials(
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.service = service
        self._sheet_ids: dict[str, int] = {}
        self.refresh_sheet_ids()

    @property
    def values(self) -> Any:
        return self.service.spreadsheets().values()

    def refresh_sheet_ids(self) -> None:
        metadata = self._execute(self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets.properties(sheetId,title)",
        ))
        self._sheet_ids = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in metadata.get("sheets", [])
        }

    def ensure_tabs(self, titles: Iterable[str]) -> None:
        missing = [title for title in titles if title not in self._sheet_ids]
        if not missing:
            return
        requests = [{"addSheet": {"properties": {"title": title}}} for title in missing]
        self._execute(self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ), retry_safe=False)
        self.refresh_sheet_ids()

    @staticmethod
    def _retry_after(error: HttpError) -> float | None:
        headers = getattr(error, "resp", None)
        value = headers.get("retry-after") if headers is not None and hasattr(headers, "get") else None
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @classmethod
    def _execute(cls, request: Any, retry_safe: bool = True) -> Any:
        for attempt in range(SHEETS_RETRIES + 1):
            try:
                return request.execute()
            except HttpError as error:
                status = getattr(getattr(error, "resp", None), "status", 0)
                transient = status == 429 or 500 <= status <= 599
                if not retry_safe or not transient or attempt >= SHEETS_RETRIES:
                    raise
                retry_after = cls._retry_after(error)
                delay = retry_after if retry_after is not None else min(2**attempt + random.random(), 30.0)
                time.sleep(min(delay, 60.0))

    @staticmethod
    def quote(title: str) -> str:
        return "'" + title.replace("'", "''") + "'"

    def get_values(self, title: str, range_suffix: str) -> list[list[Any]]:
        response = self._execute(self.values.get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.quote(title)}!{range_suffix}",
        ))
        return response.get("values", [])

    def update_values(self, title: str, start_cell: str, rows: list[list[Any]]) -> None:
        if not rows:
            return
        sanitized = [["" if value is None else value for value in row] for row in rows]
        match = re.fullmatch(r"([A-Z]+)(\d+)", start_cell)
        if not match:
            raise ValueError(f"Unsupported start cell: {start_cell}")
        column, first_row_text = match.groups()
        next_row = int(first_row_text)
        batch: list[list[Any]] = []
        batch_chars = 0
        for row in sanitized:
            row_chars = sum(len(str(value)) for value in row)
            if batch and (len(batch) >= 250 or batch_chars + row_chars > 2_500_000):
                self._execute(self.values.update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{self.quote(title)}!{column}{next_row}",
                    valueInputOption="RAW",
                    body={"values": batch},
                ))
                next_row += len(batch)
                batch = []
                batch_chars = 0
            batch.append(row)
            batch_chars += row_chars
        if batch:
            self._execute(self.values.update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.quote(title)}!{column}{next_row}",
                valueInputOption="RAW",
                body={"values": batch},
            ))

    def clear_values(self, title: str, range_suffix: str) -> None:
        self._execute(self.values.clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.quote(title)}!{range_suffix}",
            body={},
        ))

    def instructions(self, title: str, description: str, header: list[str]) -> list[list[Any]]:
        last_column = column_name(max(1, len(header)))
        return [
            ["AGENT INSTRUCTIONS", description],
            ["LAST_UPDATED_IST", iso_ist()],
            ["READ_RANGE", f"{self.quote(title)}!A{DATA_START_ROW}:{last_column}"],
            ["ORDER", "Newest/recent records are first. Headers are on row 9."],
            ["MISSING VALUES", "Blank or NA means Garmin returned no usable value; raw payloads are retained."],
            ["RAW CHUNKS", "Concatenate payload chunks with the same record_id in chunk_index order."],
            ["DO NOT READ PARTIAL RUNS", "Check README sync_status; trust new data only when SUCCESS or PARTIAL."],
            [],
        ]

    def replace_table(
        self,
        title: str,
        header: list[str],
        rows: list[list[Any]],
        description: str,
    ) -> None:
        self.ensure_tabs([title])
        payload = self.instructions(title, description, header) + [header] + rows
        self._format_table(title, len(header), len(payload) + 10)
        self.update_values(title, "A1", payload)
        self.clear_values(title, f"A{len(payload) + 1}:AZ")

    def read_table(self, title: str) -> tuple[list[str], list[list[Any]]]:
        if title not in self._sheet_ids:
            return [], []
        values = self.get_values(title, f"A{HEADER_ROW}:AZ")
        if not values:
            return [], []
        header = [str(value) for value in values[0]]
        width = len(header)
        rows = [row + [""] * (width - len(row)) for row in values[1:] if any(value != "" for value in row)]
        return header, rows

    def upsert_rows(
        self,
        title: str,
        header: list[str],
        incoming: list[list[Any]],
        key_column: int,
        sort_columns: tuple[int, ...],
        description: str,
    ) -> tuple[int, int]:
        current_header, current_rows = self.read_table(title)
        if current_header and current_header != header:
            raise RuntimeError(f"Unexpected schema in {title}; refusing to overwrite it")
        by_key = {str(row[key_column]): row for row in current_rows if len(row) > key_column}
        changed = 0
        for row in incoming:
            key = str(row[key_column])
            if key not in by_key or by_key[key] != row:
                changed += 1
            by_key[key] = row
        rows = list(by_key.values())
        rows.sort(key=lambda row: tuple(str(row[index]) for index in sort_columns), reverse=True)
        self.replace_table(title, header, rows, description)
        return len(incoming), changed

    def replace_chunked_records(
        self,
        title: str,
        header: list[str],
        incoming: list[list[Any]],
        touched_record_ids: set[str],
        description: str,
        sha_column: int = 7,
    ) -> tuple[int, int, list[list[Any]]]:
        current_header, current_rows = self.read_table(title)
        if current_header and current_header != header:
            raise RuntimeError(f"Unexpected schema in {title}; refusing to overwrite it")

        old_hashes: dict[str, str] = {}
        old_observed: dict[str, str] = {}
        for row in current_rows:
            if len(row) > max(2, sha_column):
                old_hashes.setdefault(str(row[1]), str(row[sha_column]))
                old_observed.setdefault(str(row[1]), str(row[2]))

        kept = [row for row in current_rows if len(row) <= 1 or str(row[1]) not in touched_record_ids]
        rows = kept + incoming
        # Stable sorts keep the newest records first while preserving chunk
        # order inside each record for easy reconstruction.
        rows.sort(key=lambda row: int(row[8]) if str(row[8]).isdigit() else 0)
        rows.sort(key=lambda row: str(row[1]))
        rows.sort(key=lambda row: str(row[2]), reverse=True)
        self.replace_table(title, header, rows, description)

        new_hashes: dict[str, str] = {}
        new_observed: dict[str, str] = {}
        for row in incoming:
            if len(row) > max(2, sha_column):
                new_hashes.setdefault(str(row[1]), str(row[sha_column]))
                new_observed.setdefault(str(row[1]), str(row[2]))
        changes: list[list[Any]] = []
        for record_id, digest in new_hashes.items():
            old_digest = old_hashes.get(record_id)
            if old_digest != digest:
                changed_at = iso_ist()
                changes.append(
                    [
                        f"{record_id}|{changed_at}|{uuid.uuid4()}",
                        changed_at,
                        record_id,
                        new_observed.get(record_id, old_observed.get(record_id, "")),
                        title,
                        "NEW" if old_digest is None else "UPDATED",
                        old_digest or "",
                        digest,
                    ]
                )
        return len(incoming), len(changes), changes

    def existing_record_ids(self, title: str) -> set[str]:
        if title not in self._sheet_ids:
            return set()
        rows = self.get_values(title, f"B{DATA_START_ROW}:J")
        chunks_by_id: dict[str, tuple[int, set[int], int]] = {}
        for row in rows:
            if len(row) < 9 or not row[0]:
                continue
            record_id = str(row[0])
            try:
                chunk_index, chunk_count = int(row[7]), int(row[8])
            except (TypeError, ValueError):
                continue
            previous_count, indexes, row_count = chunks_by_id.get(record_id, (chunk_count, set(), 0))
            indexes.add(chunk_index)
            chunks_by_id[record_id] = (min(previous_count, chunk_count), indexes, row_count + 1)
        return {
            record_id
            for record_id, (chunk_count, indexes, row_count) in chunks_by_id.items()
            if chunk_count > 0 and row_count == chunk_count and indexes == set(range(1, chunk_count + 1))
        }

    def insert_new_chunked_records(
        self,
        title: str,
        header: list[str],
        incoming: list[list[Any]],
        description: str,
        sha_column: int,
    ) -> tuple[int, int, list[list[Any]]]:
        """Insert immutable records once, without rereading large payload cells."""
        if title not in self._sheet_ids:
            self.replace_table(title, header, [], description)
        header_values = self.get_values(title, f"A{HEADER_ROW}:K{HEADER_ROW}")
        current_header = [str(value) for value in header_values[0]] if header_values else []
        if current_header and current_header != header:
            raise RuntimeError(f"Unexpected schema in {title}; refusing to overwrite it")
        complete_ids = self.existing_record_ids(title)
        incoming_ids = {str(row[1]) for row in incoming}
        existing_rows = self.get_values(title, f"B{DATA_START_ROW}:J")
        delete_rows: list[int] = []
        for offset, row in enumerate(existing_rows):
            record_id = str(row[0]) if row else ""
            if not record_id or (record_id in incoming_ids and record_id not in complete_ids):
                delete_rows.append(DATA_START_ROW + offset - 1)
        if delete_rows:
            # Remove stale partial chunks and blank rows left by an interrupted
            # earlier insert before retrying the complete record.
            groups: list[tuple[int, int]] = []
            for row_index in sorted(set(delete_rows)):
                if groups and row_index == groups[-1][1]:
                    groups[-1] = (groups[-1][0], row_index + 1)
                else:
                    groups.append((row_index, row_index + 1))
            requests = [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": self._sheet_ids[title],
                            "dimension": "ROWS",
                            "startIndex": start,
                            "endIndex": end,
                        }
                    }
                }
                for start, end in sorted(groups, reverse=True)
            ]
            self._execute(
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": requests},
                ),
                retry_safe=False,
            )
        incoming = [row for row in incoming if str(row[1]) not in complete_ids]
        if not incoming:
            return 0, 0, []
        incoming.sort(key=lambda row: int(row[8]) if str(row[8]).isdigit() else 0)
        incoming.sort(key=lambda row: str(row[1]))
        incoming.sort(key=lambda row: str(row[2]), reverse=True)
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": self._sheet_ids[title],
                                "dimension": "ROWS",
                                "startIndex": DATA_START_ROW - 1,
                                "endIndex": DATA_START_ROW - 1 + len(incoming),
                            },
                            "inheritFromBefore": False,
                        }
                    }
                ]
            },
        ).execute()
        self.update_values(title, f"A{DATA_START_ROW}", incoming)
        changes: list[list[Any]] = []
        seen: set[str] = set()
        for row in incoming:
            record_id = str(row[1])
            if record_id in seen:
                continue
            seen.add(record_id)
            changed_at = iso_ist()
            changes.append(
                [
                    f"{record_id}|{changed_at}|{uuid.uuid4()}",
                    changed_at,
                    record_id,
                    str(row[2]),
                    title,
                    "NEW",
                    "",
                    str(row[sha_column]),
                ]
            )
        return len(incoming), len(changes), changes

    def read_state(self) -> dict[str, str]:
        header, rows = self.read_table("Sync State")
        if header and header != STATE_HEADER:
            raise RuntimeError("Unexpected schema in Sync State")
        return {str(row[0]): str(row[1]) for row in rows if len(row) >= 2}

    def write_state(self, values: dict[str, tuple[str, str]]) -> None:
        _, current_rows = self.read_table("Sync State")
        by_key = {str(row[0]): row for row in current_rows if row}
        for key, (value, notes) in values.items():
            by_key[key] = [key, value, iso_ist(), notes]
        rows = sorted(by_key.values(), key=lambda row: str(row[0]))
        self.replace_table(
            "Sync State",
            STATE_HEADER,
            rows,
            "Machine-readable resumability state. Agents normally do not need this tab.",
        )

    def open_retry_days(self, limit: int = 5) -> list[str]:
        header, rows = self.read_table("Errors")
        if header and header != ERROR_HEADER:
            raise RuntimeError("Unexpected schema in Errors")
        days: list[str] = []
        for row in rows:
            if len(row) > 5 and str(row[1]) == "OPEN" and str(row[5]) and str(row[5]) not in days:
                days.append(str(row[5]))
            if len(days) >= limit:
                break
        return days

    def update_errors(self, failures: list[SyncFailure], successful_keys: set[str]) -> None:
        header, rows = self.read_table("Errors")
        if header and header != ERROR_HEADER:
            raise RuntimeError("Unexpected schema in Errors")
        by_key = {str(row[0]): row for row in rows if row}
        timestamp = iso_ist()
        for key in successful_keys:
            if key in by_key:
                by_key[key][1] = "RESOLVED"
                by_key[key][7] = timestamp
        for failure in failures:
            old = by_key.get(failure.error_key)
            first_seen = old[6] if old and len(old) > 6 else timestamp
            attempts = int(old[8]) + 1 if old and len(old) > 8 and str(old[8]).isdigit() else 1
            by_key[failure.error_key] = [
                failure.error_key,
                "OPEN" if failure.retryable else "UNSUPPORTED",
                failure.category,
                failure.dataset,
                failure.record_key,
                failure.observed_on,
                first_seen,
                timestamp,
                attempts,
                failure.error_type,
                failure.message,
            ]
        sorted_rows = sorted(by_key.values(), key=lambda row: str(row[7]), reverse=True)
        self.replace_table(
            "Errors",
            ERROR_HEADER,
            sorted_rows,
            "Failures and unsupported Garmin endpoints. OPEN records are retried automatically.",
        )

    def append_changes(self, incoming: list[list[Any]]) -> None:
        self.upsert_rows(
            "Changes",
            CHANGE_HEADER,
            incoming,
            key_column=0,
            sort_columns=(1, 3),
            description="Append-only NEW or UPDATED source-record audit history, newest first.",
        )

    def append_sync_log(self, row: list[Any]) -> None:
        self.upsert_rows(
            "Sync Log",
            SYNC_LOG_HEADER,
            [row],
            key_column=2,
            sort_columns=(0,),
            description="One row per scheduled execution, newest first, including successes and failures.",
        )

    def write_readme(
        self,
        status: str,
        started_at_ist: str,
        completed_at_ist: str,
        last_success_summary: str,
        last_failure_summary: str,
    ) -> None:
        self.ensure_tabs(["README"])
        rows = [
            ["GARMIN HEALTH LIVE", "Agent-readable Garmin Connect archive"],
            ["sync_status", status],
            ["sync_started_at_ist", started_at_ist],
            ["sync_completed_at_ist", completed_at_ist],
            ["what_succeeded", last_success_summary],
            ["what_failed", last_failure_summary],
            ["how_to_read", "Read the latest YYYY-MM Daily and Activities tabs first. Data starts at row 10 and newest rows are first."],
            ["raw_data", "Raw and Activity Raw tabs contain complete JSON. Concatenate chunks sharing record_id by chunk_index."],
            ["activity_files", "Files tabs contain exact base64 Garmin ORIGINAL ZIP bytes. Concatenate chunks and base64-decode."],
            ["heart_rate", "Daily hourly_hr_local_actual_samples lists every hour average, actual sample count, and workout labels. Raw samples remain in Raw JSON."],
            ["partial_runs", "Use data from a PARTIAL run, but inspect Errors for missing endpoints. Do not trust RUNNING as complete."],
            ["timezone", "Asia/Kolkata (IST)"],
            ["privacy", "This workbook contains sensitive biometric and location data. Do not make it public."],
        ]
        self.update_values("README", "A1", rows)
        self.clear_values("README", f"A{len(rows) + 1}:Z")

    def _format_table(self, title: str, column_count: int, row_count: int) -> None:
        sheet_id = self._sheet_ids[title]
        requests = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {
                            "frozenRowCount": HEADER_ROW,
                            "rowCount": max(1000, row_count),
                            "columnCount": max(52, column_count),
                        },
                    },
                    "fields": "gridProperties(frozenRowCount,rowCount,columnCount)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": HEADER_ROW - 1,
                        "endRowIndex": HEADER_ROW,
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
        ]
        self._execute(self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ))


def column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
