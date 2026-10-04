# Garmin Agent Sync

A self-hosted example for syncing your own Garmin Connect data into a Google Sheet you control. The workbook can then be shared with your coding assistant or training agent for analysis.

This repository includes the Firebase Functions deployment that is currently working for the project this example is based on. It also includes a local runner for Windows and Linux servers. These are personal-account integrations using unofficial Garmin Connect endpoints; Garmin can change or rate-limit them at any time.

## Choose where it runs

| Option | What runs | Setup guide | Status |
| --- | --- | --- | --- |
| Firebase Functions | Scheduled Google Cloud Function; state and data in your Sheet; Garmin session in Secret Manager | [Firebase Functions](docs/deployment/firebase-functions.md) | Existing working deployment, generalized for your project |
| Windows server | The same Sheets sync engine, started manually or by Task Scheduler | [Windows server](docs/deployment/windows-server.md) | Local runner included |
| Linux server | The same Sheets sync engine, started manually or by systemd timer; optional Docker Compose wrapper | [Linux server](docs/deployment/linux-server.md) and [Docker](docs/deployment/docker-server.md) | Local runner and container example included |
| Another cloud provider | Adapt the thin runtime wrapper to provide secrets, Google credentials, persistent state, scheduling, and a single-run guard | [Provider adapter template](docs/deployment/provider-adapter-template.md) | Contract documented; other providers are not tested here |

The sync writes to one spreadsheet per installation. The local SQLite archive is a separate companion tool for lossless personal archiving and does not feed this Sheets sync automatically.

## Start here

1. Choose a deployment from the table above.
2. Read [the data and privacy guide](docs/data-flow-and-privacy.md) before enabling cloud storage. The workbook can contain sensitive health and location data.
3. Create your own private Google Sheet and grant the runtime access as described in [Google Sheets setup](docs/google-sheets.md).
4. Follow the selected deployment guide. Never paste Garmin passwords, session tokens, Google credential files, or secret values into chat, an agent prompt, source code, screenshots, or Git.

For a local Windows or Linux run, use the [self-hosted setup instructions](docs/deployment/windows-server.md) or [Linux server guide](docs/deployment/linux-server.md). The common commands are:

```text
python functions/local_run.py login
python functions/local_run.py doctor
python functions/local_run.py sync --dry-run
python functions/local_run.py sync
python functions/local_run.py status
```

To add a one-time local date window, pass both inclusive bounds, for example `python functions/local_run.py sync --start 2026-09-01 --end 2026-09-07`. Preview the same window by adding `--dry-run`. Manual windows do not reset or advance the automatic historical backfill cursor.

`login` prompts in the terminal and supports Garmin's interactive MFA prompt. On Linux, the reusable session is a file readable only by the service user; on Windows, it uses the Windows DPAPI-protected store. The local runner does not put the Garmin password in the config file.

## What is what

| Path | Responsibility |
| --- | --- |
| `functions/cloud_sync.py` | Garmin API adapter, date collection, response normalization, retries, and Sheets row mapping |
| `functions/sync_service.py` | Shared sync orchestration, backfill planning, correction window, retries, and run status |
| `functions/sheets_store.py` | Google Sheets authentication and batch/table persistence |
| `functions/local_run.py` | Windows/Linux CLI for login, doctor, dry-run, sync, and status |
| `functions/main.py` | Firebase scheduled-function wrapper, Secret Manager access, and cloud runtime settings |
| `garmin_db.py` | Optional local SQLite health archive and Garmin Account Data Export import |
| `sync_garmin.py`, `export_activity_history.py` | Optional local Markdown and activity-history utilities; these do not write to Sheets |
| `scripts/set_cloud_token.py` | Copies the locally entered Garmin session into Firebase Secret Manager without printing it |
| `docs/` | Architecture, privacy, configuration, operations, and deployment instructions |
| `examples/` | Synthetic configuration and schema examples only |

See [the architecture overview](docs/architecture.md) for data flow and sync ordering, [the threat model](docs/threat-model.md) for trust boundaries and risks, and [the configuration reference](docs/configuration.md) for settings and defaults.

## How the sync behaves

- The collector reads Garmin Connect; it has no Garmin write calls.
- The default cloud schedule is 11:00 and 21:00 Asia/Kolkata. Local schedules are chosen by the operator.
- Each run refreshes recent days, advances historical backfill in bounded batches, and retries open failures.
- Activities are keyed by Garmin activity ID. Daily rows are keyed by date. Raw responses are chunked into monthly tabs; original activity archives are stored as base64 chunks.
- Sync state, run history, errors, and changes are kept in the workbook. The README tab is written last as the run commit marker (`SUCCESS`, `PARTIAL`, or `FAILED`). Reruns upsert rows by stable keys.
- A run can partially update a workbook if an API or Sheets request fails. Re-running is designed to repair the same rows without adding duplicate activities.

Review the exact workbook layout and access model in [Google Sheets setup](docs/google-sheets.md).

## Adapt this example with a coding agent

You can use any coding agent. Start with [the agent setup guide](docs/agent-setup.md) and ask it to read the code map above and relevant docs before changing files. For a focused change, name the desired Garmin fields or workbook behavior and deployment target. Agents should use synthetic data, preserve stable keys and retry behavior, and must not read or repeat secret values.

The Sheets sync isolates `garminconnect` calls behind `GarminCollector`; the optional local archive currently calls it directly. The pinned upstream package is MIT licensed, but its personal-account endpoint behavior is unofficial and not guaranteed by Garmin. Local archive and Firebase dependency pins are currently separate; see [the dependency decision](docs/decisions/0001-dependency-and-ownership.md) before changing or consolidating them.

## Safety and project status

- This is a reference project, not an official Garmin integration, hosted service, medical device, or medical advice.
- You own the Garmin account, Google project, spreadsheet, credentials, schedule, backups, and exported data.
- No production Garmin credentials, exported activity files, private sheets, or logs belong in this repository.
- Firebase is the established deployment. Windows and Linux runners are included as self-hosted examples; run the documented checks and validate your own Google/Garmin access before scheduling them.
- The project license is not yet selected. Review [the dependency and ownership decision](docs/decisions/0001-dependency-and-ownership.md) and choose a repository license before publishing a release.

## Companion local archive

The SQLite archive is optional and separate from the Sheets deployment. It imports Garmin Account Data Export ZIP files and stores raw records locally under `garmin/garmin.sqlite3`. Follow [the local archive guide](docs/local-archive.md) if you want that capability. Do not upload or commit the database; it can contain health, reproductive-health, and precise location data.
