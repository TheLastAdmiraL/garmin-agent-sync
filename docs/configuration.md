# Configuration reference

Create a local `.env` from [`.env.example`](../.env.example). The file contains ordinary configuration only and is Git-ignored. Never place Garmin passwords, Garmin session JSON, OAuth refresh tokens, service-account private keys, or private key material in it.

| Name | Type | Default | Required | Where it belongs / meaning |
| --- | --- | --- | --- | --- |
| `GARMIN_SHEET_ID` | string | none | yes | `.env` for local/Docker; Firebase parameter file for cloud. Spreadsheet ID from the URL between `/d/` and `/edit`. |
| `GARMIN_BACKFILL_START` | `YYYY-MM-DD` | none for local; `2024-01-01` in Firebase parameter declaration | yes | `.env` or Firebase parameter file. First Garmin calendar date to include on initial backfill; choose the date you need. |
| `GARMIN_BACKFILL_BATCH_DAYS` | positive integer | `7` | no | `.env` or Firebase parameter file. Historical days advanced per execution; keep bounded for time/quota limits. |
| `GARMIN_REFRESH_DAYS` | positive integer | `3` | no | `.env` or Firebase parameter file. Recent dates fetched again to capture late Garmin updates. |
| `GOOGLE_APPLICATION_CREDENTIALS` | filesystem path | ADC discovery | no | Local/Docker only. Path to a protected service-account credential JSON; keep the file outside Git. |
| `RUNTIME_SERVICE_ACCOUNT` | service-account email | edit `functions/main.py` | Firebase | Dedicated runtime identity email. Replace the `YOUR_PROJECT_ID` placeholder before deployment. |
| `SYNC_UID` / `SYNC_GID` | integer | `10001` | Docker only | Set in `.env` to the host Linux sync user's numeric UID/GID so bind-mounted credentials and persistent state have matching permissions. |

Values in the process environment override the `.env` file. The local runner validates the sheet ID, start date, and positive day counts before sync. For an alternate `.env` path, pass `--config PATH` before the command.

For a one-time local date window, pass both `sync --start YYYY-MM-DD --end YYYY-MM-DD`; the bounds are inclusive. Add `--dry-run` to preview the combined requested ranges without contacting Garmin or writing to Sheets. This does not change the automatic backfill cursor. Keep large windows bounded to avoid long runtimes and Garmin rate limits.

Garmin daily dates and `updated_at_ist` use Asia/Kolkata in the shared sync engine. The Firebase schedule is explicitly interpreted in Asia/Kolkata. Task Scheduler follows the Windows machine time zone; systemd timer examples name `Asia/Kolkata` explicitly. Pick local trigger times with the server's configured timezone in mind; the workbook timestamps remain IST.

## Google credentials

On a private Windows/Linux host, configure ADC using a protected service-account file path in `GOOGLE_APPLICATION_CREDENTIALS` or an ADC login/impersonation workflow with the Sheets scope. The path may be stored in `.env`; the credential file itself must be outside the repository. Make sure the service identity has been shared on the target spreadsheet.

On Firebase, use the attached runtime identity; do not upload a service-account key to the function. The Garmin session is configured separately as Firebase secret `GARMIN_TOKEN`.
