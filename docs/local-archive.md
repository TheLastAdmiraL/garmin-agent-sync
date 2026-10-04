# Optional local SQLite archive

The local archive is independent of the Google Sheets sync. It imports Garmin Account Data Export files and incrementally stores Garmin responses in a local SQLite database. It is useful as a private archive or completeness baseline; it does not populate the Google Sheet.

The current `garmin_db.py` implementation uses the Windows DPAPI token store from `token_store.py`, so its authenticated sync workflow is Windows-specific. For Windows setup, create an environment from `requirements.lock` (Windows x64/Python 3.12), install it with `python -m pip install --require-hashes -r requirements.lock`, authenticate with `sync_garmin.py --login`, then use:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python.exe sync_garmin.py --login
.venv\Scripts\python.exe garmin_db.py import-export "C:\path\to\your-garmin-export.zip"
.venv\Scripts\python.exe garmin_db.py sync --start YYYY-MM-DD
.venv\Scripts\python.exe garmin_db.py sync
.venv\Scripts\python.exe garmin_db.py status
```

The database defaults to `garmin/garmin.sqlite3`, which is Git-ignored. You can change it with `--database PATH`. Imports use content hashes and are safe to rerun. The SQLite database is not encrypted by this project; store it on an encrypted disk and protect backups like the Google workbook. The archive preserves original files and sensitive raw data. See the CLI parser in `garmin_db.py` for all options.
