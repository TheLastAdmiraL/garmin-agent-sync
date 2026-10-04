# Windows server deployment

Use this option when a Windows PC/server stays powered on and can reach Garmin Connect and Google APIs. It runs the same `functions/sync_service.py` engine as Firebase. Task Scheduler starts one local sync process; the process has an OS-level lock to prevent overlap.

## Install

1. Install Python 3.12 or later and clone/download this repository to a stable path, for example `C:\GarminAgentSync`.
2. Open PowerShell in the repository and create the Functions environment:

   ```powershell
   py -3.12 -m venv functions\.venv
   functions\.venv\Scripts\python.exe -m pip install --upgrade pip
   functions\.venv\Scripts\python.exe -m pip install -r functions\requirements-local.txt
   Copy-Item .env.example .env
   notepad .env
   ```

3. Set your sheet ID and backfill start date in `.env`. Keep `.env` out of Git.
4. Create Google credentials. Prefer an attached/workload identity where available. Otherwise use a dedicated service-account key file outside the repo, restrict its Windows ACL to the sync user, and add only its path to `.env`, for example `GOOGLE_APPLICATION_CREDENTIALS=C:\private\google-sheets-service-account.json`. Share only the target workbook with the service-account email as Editor.
5. Log in to Garmin in the interactive terminal:

   ```powershell
   functions\.venv\Scripts\python.exe functions\local_run.py login
   ```

   The session is stored with Windows DPAPI for the current user. Task Scheduler must run as that same Windows user. Do not run login under a different administrator/service account and expect the token to decrypt.
6. Check access and preview the run:

   ```powershell
   functions\.venv\Scripts\python.exe functions\local_run.py doctor
   functions\.venv\Scripts\python.exe functions\local_run.py sync --dry-run
   ```

7. Run once manually, then inspect the workbook before scheduling:

   ```powershell
   functions\.venv\Scripts\python.exe functions\local_run.py sync
   functions\.venv\Scripts\python.exe functions\local_run.py status
   ```

## Schedule with Task Scheduler

Create a task running as the same account used for login. Configure an action:

- Program: `powershell.exe`
- Arguments: `-NoProfile -File "C:\GarminAgentSync\infra\windows\run-sync.ps1"`
- Start in: `C:\GarminAgentSync`

Set triggers for the time(s) you want. Task Scheduler uses the Windows machine's time zone. Choose a time after the watch has uploaded to Garmin Connect, and leave enough runtime margin. Enable a missed-run policy if desired, but avoid launching parallel instances; the lock rejects overlaps. Do not configure automatic retry loops for permanent auth or permission errors. The wrapper keeps a private per-user log at `%LOCALAPPDATA%\GarminAgentSync\logs\sync.log` and rotates one file at 5 MB; Task Scheduler history also records the process exit code.

## Status, reauthentication, backup, and removal

Use `status` for the workbook checkpoint and status. If the session expires, sign in to the same Windows account and run `reauth`. Back up `.env`, the workbook, and any Google credential file securely; the Garmin DPAPI token is bound to the user/machine protection state. For removal, disable/delete the task, revoke workbook sharing, remove session and Google credential files, then follow [the operations teardown](../operations-and-troubleshooting.md).
