# Operations and troubleshooting

## Routine checks

- Run `python functions/local_run.py status` to inspect the workbook status and next backfill date.
- Run `python functions/local_run.py doctor` after changing Google credentials, spreadsheet sharing, or local session storage.
- Check the workbook's latest `Sync Log` and `Errors` entries after each scheduled run.
- Monitor Google Sheets workbook size; raw health data and base64 activity files consume cells.
- Check Garmin session health after password changes, MFA changes, long idle periods, or authentication failures.
- Expect execution time to depend on the number of dates, endpoints, activities, retries, and Google write volume. The Firebase function has a 15-minute timeout; local runs have no application timeout. Use small backfill batches and inspect run duration before increasing workload.
- Garmin calls have a 450 ms delay and up to three attempts. This is a polite baseline, not a quota guarantee; reduce batch size and allow cooldowns if Garmin rate-limits the account.

## Common recovery steps

| Symptom | Action |
| --- | --- |
| Garmin login/session failure | Run `python functions/local_run.py reauth` interactively. Update Firebase `GARMIN_TOKEN` if cloud deployed. |
| Linux session permission check fails | Set the state directory to mode `0700` and `garmin-token.json` to `0600`, then rerun `doctor`. |
| Google API permission denied | Confirm the configured identity and share the exact private workbook with its service-account email as Editor. Check Sheets API access. |
| `Unexpected schema` | Stop scheduled runs. Export/backup the workbook, compare the affected tab with the headers in source, then restore the expected header or perform an explicit migration. |
| Open retry errors remain | Review endpoint, date, and error category. Retryable errors are included automatically; unsupported endpoints require code/library changes or can be omitted. |
| Backfill seems stalled | Inspect `backfill_next_date` and recent `Sync Log` rows. Increase the batch only after checking function timeout and Garmin quota behavior. |
| Need a local one-off date range | Run `python functions/local_run.py sync --start YYYY-MM-DD --end YYYY-MM-DD`; preview with `--dry-run`. This adds dates to the regular refresh/backfill work without changing the cursor. |
| `RUNNING` remains after a crash | Inspect logs and workbook tabs. Rerun after confirming no other process is active; idempotent keys repair partial table writes. |
| Local process reports another sync is running | Confirm no sync process is active. The OS releases the lock on process exit; do not delete the lock while a process may hold it. |
| Sheet is near the cell limit | Export a backup. Partition older month tabs into another private workbook and preserve a manifest before deleting old tabs. |

## Backup, restore, and upgrade

Export a private workbook copy before schema or code upgrades. Back up Linux state and Google ADC data separately, encrypted and access-controlled. On Windows, session protection is tied to the Windows user and DPAPI state; a copied token file may not decrypt under a different account or machine. For restore, reinstall the documented dependency set, restore the workbook/state and credentials, run `doctor`, and perform a dry run before sync.

For upgrades, review dependency changelogs, run the documented test suite in a disposable environment, deploy/run once manually, inspect `Errors` and `Sync Log`, then resume the schedule. Retain the prior release/configuration until the new run is confirmed.

## Teardown

1. Disable Task Scheduler, systemd timer, Compose scheduler, or Cloud Scheduler.
2. Remove Firebase Functions and related scheduler resources if applicable.
3. Revoke the runtime identity's workbook share and secret access.
4. Delete Garmin session files or Firebase Secret Manager versions and revoke the Garmin session where possible.
5. Export and then delete the Google workbook if you no longer need the data.
6. Remove local SQLite databases, backups, and credential files using your storage provider's retention/deletion procedure.
