# Google Sheets setup and data contract

## Authorization

Create a private spreadsheet in your Google account. The sync uses the Sheets API with the `https://www.googleapis.com/auth/spreadsheets` scope and reads/writes only through the spreadsheet ID configured for the deployment. It does not request a broad Google Drive scope or create files in Drive.

If you want a coding or training agent to analyze the workbook, connect that agent to your Google account or provide a private export through the agent's own supported workflow. This project does not connect the agent to Drive, proxy workbook contents through a maintainer, or make the workbook public.

For a service-account setup, share only the target spreadsheet with the service-account email as Editor. Do not make the workbook public or grant broad Drive access. Firebase uses its attached service account. A Windows/Linux process uses Application Default Credentials (ADC), normally from a service-account credential file or a configured local ADC identity. Keep credential files outside the repository and readable only by the sync user. Plain `gcloud auth application-default login` may not grant scopes for APIs outside Google Cloud; Google documents using an OAuth client ID plus explicit scopes, or service-account impersonation with Sheets scope, for that case. See [Google's ADC setup guide](https://cloud.google.com/docs/authentication/set-up-adc-local-dev-environment).

The `spreadsheets` OAuth scope is broad across spreadsheet files the identity can access. The code sends requests only to the configured spreadsheet ID, but the scope itself is not limited to one file. A dedicated service account shared only on this workbook gives the clearest access boundary. No Drive API scope is requested.

## Workbook layout

- `README`: latest sync status and reading instructions. `SUCCESS` and `PARTIAL` are completed batches; `RUNNING` is not a commit marker.
- `YYYY-MM Daily`: one summary row per requested date, newest dates first.
- `YYYY-MM Activities`: one summary row per Garmin activity ID, newest first.
- `YYYY-MM Raw`: chunked lossless daily and snapshot JSON.
- `YYYY-MM Activity Raw`: chunked activity endpoint JSON.
- `YYYY-MM Files`: chunked base64 original activity bytes.
- `Sync State`: backfill cursor, recent ranges, and run identifiers.
- `Sync Log`: one row per run.
- `Errors`: endpoint failures and unsupported operations; retryable failures are retried.
- `Changes`: source record hashes that changed.

Tabular headers are on row 9 and data starts on row 10. The headers are declared in `functions/cloud_sync.py` and `functions/sheets_store.py`. Raw data chunks are limited to 40,000 characters per cell and joined by `record_id` in `chunk_index` order.

## Summary table schema

These are the exact current headers (order matters; the code refuses an unexpected header):

```text
Daily: date, updated_at_ist, steps, distance_m, calories, floors_ascended, intensity_minutes, resting_hr_bpm, average_stress, body_battery_low, body_battery_high, sleep_seconds, sleep_hours, sleep_score, hrv_overnight_ms, training_readiness, training_status, vo2_max, average_spo2, average_respiration, hourly_hr_local_actual_samples, activity_intervals_local, activity_count, activity_ids, source_hashes_json

Activities: start_time_local, date, activity_id, activity_type, name, duration_seconds, distance_m, average_hr_bpm, max_hr_bpm, calories, elevation_gain_m, average_speed_mps, average_cadence, average_power_w, aerobic_training_effect, anaerobic_training_effect, summary_sha256, full_summary_json

Raw and Activity Raw: unique_chunk_key, record_id, observed_on, category, dataset, record_key, fetched_at_ist, sha256, chunk_index, chunk_count, payload_json_chunk

Files: unique_chunk_key, record_id, observed_on, activity_id, format, fetched_at_ist, sha256, byte_length, chunk_index, chunk_count, base64_chunk
```

Numeric columns use Garmin's units shown in the header: meters, seconds, bpm, m/s, watts, kilocalories, and ms. `updated_at_ist` and `fetched_at_ist` are ISO timestamps with the IST offset. `date` and `observed_on` are calendar dates; activity start time is Garmin's local timestamp when present. Text and JSON columns remain strings. Missing values are written as blank; complete source JSON is retained in the raw tabs. The `average_cadence` value follows Garmin's source field and may not have a uniform meaning across sports. Training effect and proprietary scores retain Garmin's scale without conversion.

## Stable keys and update behavior

- Daily summary key: local Garmin calendar date (`YYYY-MM-DD`). A rerun replaces the same date's selected summary fields.
- Activity summary key: Garmin `activityId`. If Garmin revises an activity, the selected summary row is overwritten for that ID.
- Raw response key: category, dataset, and Garmin date/activity key. Existing chunks for touched records are replaced.
- Original activity files are inserted once by activity ID. The sync checks chunk indexes/counts; an incomplete file from a partial write is fetched again and its partial rows are replaced. A complete stored original archive is not overwritten on later runs.
- Sync state is advanced after record tables, errors, and changes have been written. The final `README` marker is written after the run log.

The API cannot atomically update multiple tabs. If a run stops partway, rerun it; inspect `Errors` and the last `Sync Log` row. Back up/export the workbook before manually changing headers or state. The sync refuses unexpected table headers rather than silently overwriting them.

If the workbook or tab is deleted, renamed, manually sorted, or near the Sheets cell limit, stop scheduled runs and make a backup before repair. Read [operations and troubleshooting](operations-and-troubleshooting.md) for recovery steps.
