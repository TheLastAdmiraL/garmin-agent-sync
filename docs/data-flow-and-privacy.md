# Data flow and privacy

## Data collected

The collector requests daily steps, distance, floors, calories, heart rate, resting heart rate, sleep/stages, stress, Body Battery, HRV, respiration, SpO2, training readiness/status, fitness/max metrics, hydration, body composition, weigh-ins, blood pressure, endurance/hill scores, lifestyle/nutrition/menstrual data where available; profile, device, zones, race-prediction and threshold snapshots; activity summaries, details, samples, splits, zones, weather, gear and exercise sets; and original activity files where supported. The exact endpoint names are in `DAILY_ENDPOINTS`, `SNAPSHOT_ENDPOINTS`, and `ACTIVITY_ENDPOINTS` in `functions/cloud_sync.py`. Garmin returns different fields depending on account, device, region, permissions, and retention. Unsupported or failed endpoints are recorded so the rest of a run can continue.

Data may include health, sleep, heart-rate, reproductive-health, nutrition, weight, and location information. Review the endpoint lists before enabling sync if you do not want particular categories collected. This code does not modify Garmin records.

## Destinations and stored state

| Data | Destination |
| --- | --- |
| Compact daily and activity summaries | Monthly tabs in your private Google Sheet |
| Raw JSON and original activity ZIP/FIT bytes | Chunked monthly tabs in the same workbook |
| Backfill cursor, run history, errors, and change history | `Sync State`, `Sync Log`, `Errors`, and `Changes` workbook tabs |
| Garmin password | Not persisted by this project |
| Reusable Garmin session | Windows DPAPI store, Linux user-only state file, or Firebase Secret Manager, depending on deployment |
| Google credential material | OS ADC store / operator-configured service-account file, or attached Firebase service account |
| Operational logs | Local terminal/system journal or Google Cloud Logging; job dates/ranges, counts, and error categories, without activity IDs or raw payloads by default |
| Optional account-export archive | Local `garmin/garmin.sqlite3`, separate from the Sheets sync; SQLite data is not encrypted by this project |

The workbook can grow quickly because it stores raw responses and original files. Google Sheets has a workbook cell limit; monitor size and use the documented archive procedure before approaching it.

## Network destinations

- Garmin Connect, using unofficial private personal-account endpoints through `garminconnect`.
- Google Sheets API for workbook reads and writes.
- Google Secret Manager from the Firebase deployment to read and rotate the Garmin session.
- Google Cloud Scheduler to invoke Firebase Functions on schedule.

The local runner does not send data to a project operated by this repository's maintainer. It writes to the spreadsheet and Google project you configure.

## Data minimization and deletion

Choose the earliest backfill date you need. Reduce the endpoint lists if you do not want particular metrics. Restrict workbook sharing and avoid public links. To remove the cloud copy, delete the workbook and revoke the runtime identity's access. Delete/revoke the stored Garmin session separately; removing a file or Secret Manager version does not necessarily revoke a session held by Garmin. See [Garmin auth and sessions](garmin-auth-and-sessions.md) and [operations](operations-and-troubleshooting.md).
