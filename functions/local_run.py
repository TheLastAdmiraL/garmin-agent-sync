"""Run the Sheets sync from a Windows or Linux host using the same sync engine."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import tempfile
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_sync import IST  # noqa: E402
from garminconnect import Garmin  # noqa: E402
from sheets_store import GoogleSheetsStore  # noqa: E402
from sync_service import plan_sync_ranges, run_sync  # noqa: E402
from redaction import safe_error  # noqa: E402


def state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise RuntimeError("LOCALAPPDATA is unavailable; cannot choose a safe session location.")
        return Path(base) / "GarminAgentSync"
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base) / "garmin-agent-sync" if base else Path.home() / ".local" / "state" / "garmin-agent-sync"


def _linux_token_path() -> Path:
    return state_dir() / "garmin-token.json"


def save_session(token_json: str) -> Path:
    if not token_json or not token_json.lstrip().startswith("{"):
        raise ValueError("Refusing to save an invalid Garmin session.")
    if os.name == "nt":
        from token_store import save as save_dpapi_token

        return save_dpapi_token(token_json)

    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    fd, temporary_name = tempfile.mkstemp(prefix=".garmin-token-", dir=directory)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token_json)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _linux_token_path())
        return _linux_token_path()
    finally:
        if temporary.exists():
            temporary.unlink()


def load_session() -> str:
    if os.name == "nt":
        from token_store import load as load_dpapi_token

        return load_dpapi_token()
    try:
        path = _linux_token_path()
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RuntimeError("Garmin session file permissions are too broad; set directory to 0700 and file to 0600.")
        token = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("No saved Garmin session. Run `python functions/local_run.py login` first.") from error
    if not token.lstrip().startswith("{"):
        raise RuntimeError("The saved Garmin session is invalid. Run login again.")
    return token


def load_config(path: Path) -> dict[str, str]:
    """Read non-secret KEY=VALUE settings; process environment takes precedence."""
    values: dict[str, str] = {}
    if path.exists():
        for line_number, original in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = original.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"Malformed config on line {line_number}; expected KEY=VALUE.")
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not key.replace("_", "").isalnum() or not key:
                raise ValueError(f"Invalid config key on line {line_number}.")
            values[key] = value.strip("\"'")
    values.update({key: value for key, value in os.environ.items() if value is not None})
    return values


def required_config(config: dict[str, str]) -> tuple[str, str, int, int]:
    sheet_id = config.get("GARMIN_SHEET_ID", "").strip()
    if not sheet_id or any(char.isspace() for char in sheet_id):
        raise ValueError("Set GARMIN_SHEET_ID to the ID in your Google Sheets URL.")
    start = config.get("GARMIN_BACKFILL_START", "").strip()
    if not start:
        raise ValueError("Set GARMIN_BACKFILL_START to YYYY-MM-DD in the local config file.")
    try:
        date.fromisoformat(start)
        batch_days = int(config.get("GARMIN_BACKFILL_BATCH_DAYS", "7"))
        refresh_days = int(config.get("GARMIN_REFRESH_DAYS", "3"))
    except ValueError as error:
        raise ValueError("Backfill date must be YYYY-MM-DD and day counts must be integers.") from error
    if batch_days < 1 or refresh_days < 1:
        raise ValueError("Backfill batch and refresh day counts must be positive.")
    return sheet_id, start, batch_days, refresh_days


class ProcessLock:
    """An OS-level lock released automatically if the process exits or crashes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.read(1) == b"":
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another local sync is already running.") from error
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.handle is None:
            return
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def login() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError("Run login in an interactive terminal so the password can stay hidden.")
    email = input("Garmin email: ").strip()
    if not email:
        raise RuntimeError("No email entered.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("Garmin password (hidden): ")
    try:
        client = Garmin(email=email, password=password, prompt_mfa=lambda: input("Garmin 2FA code: ").strip())
        client.login()
        path = save_session(client.client.dumps())
    except Exception as error:
        raise RuntimeError(f"Garmin login failed ({type(error).__name__}); check credentials or MFA and retry.") from None
    print(f"Login succeeded. Session saved outside the repository at {path}.")


def _config_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Garmin-to-Sheets sync on this Windows or Linux host.")
    parser.add_argument("--config", default=".env", help="Path to the non-secret KEY=VALUE config file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Log in interactively and save a reusable Garmin session")
    commands.add_parser("reauth", help="Replace the saved session after Garmin invalidates it")
    commands.add_parser("doctor", help="Check local config and Google Sheets access")
    commands.add_parser("status", help="Read the latest sync marker and checkpoint from Sheets")
    sync_parser = commands.add_parser("sync", help="Run the sync")
    sync_parser.add_argument("--dry-run", action="store_true", help="Show planned date ranges without calling Garmin")
    sync_parser.add_argument("--start", help="Add a manual inclusive start date (YYYY-MM-DD); requires --end")
    sync_parser.add_argument("--end", help="Add a manual inclusive end date (YYYY-MM-DD); requires --start")
    args = parser.parse_args()

    try:
        if args.command in {"login", "reauth"}:
            login()
            return 0

        config = load_config(_config_path(args.config))
        sheet_id, backfill_start, batch_days, refresh_days = required_config(config)
        google_credentials = config.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if google_credentials and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            credential_path = Path(google_credentials).expanduser()
            if not credential_path.is_absolute():
                credential_path = ROOT / credential_path
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credential_path)
        store = GoogleSheetsStore(sheet_id)

        if args.command == "doctor":
            print("Config: valid")
            print("Google Sheets: accessible with the current application credentials")
            try:
                load_session()
                print("Garmin session: present")
            except RuntimeError as error:
                print(f"Garmin session: {error}")
                return 1
            return 0

        if args.command == "status":
            state = store.read_state()
            readme = store.get_values("README", "A1:B14") if "README" in store._sheet_ids else []
            status_value = next((row[1] for row in readme if len(row) > 1 and row[0] == "sync_status"), "unknown")
            print(f"Latest workbook status: {status_value}")
            print(f"Next backfill date: {state.get('backfill_next_date', backfill_start)}")
            print(f"Backfill complete: {state.get('backfill_complete', 'FALSE')}")
            return 0

        if args.command == "sync" and args.dry_run:
            state = store.read_state()
            manual_start = date.fromisoformat(args.start) if args.start else None
            manual_end = date.fromisoformat(args.end) if args.end else None
            ranges, next_date, complete = plan_sync_ranges(
                backfill_start,
                batch_days,
                refresh_days,
                datetime.now(IST).date(),
                state,
                store.open_retry_days(limit=5),
                manual_start,
                manual_end,
            )
            print("Planned Garmin date ranges (dry run; no Garmin calls or Sheets writes):")
            for start, end in ranges:
                print(f"  {start.isoformat()} through {end.isoformat()}")
            print(f"Next historical date after this run: {next_date.isoformat()}")
            print(f"Backfill complete after this run: {complete}")
            return 0

        if args.command == "sync":
            manual_start = date.fromisoformat(args.start) if args.start else None
            manual_end = date.fromisoformat(args.end) if args.end else None
            token_json = load_session()
            def persist_token(old_token: str, new_token: str) -> bool:
                changed = old_token != new_token
                if changed:
                    save_session(new_token)
                return changed

            with ProcessLock(state_dir() / "sync.lock"):
                result = run_sync(
                    spreadsheet_id=sheet_id,
                    token_json=token_json,
                    backfill_start=backfill_start,
                    backfill_batch_days=batch_days,
                    refresh_days=refresh_days,
                    trigger="local-cli",
                    store=store,
                    token_persister=persist_token,
                    manual_start=manual_start,
                    manual_end=manual_end,
                )
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("status") in {"SUCCESS", "PARTIAL"} else 1
    except Exception as error:
        print(f"{type(error).__name__}: {safe_error(error)}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
