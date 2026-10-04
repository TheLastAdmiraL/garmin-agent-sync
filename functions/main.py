"""Firebase entry point for Garmin Health Live."""

from __future__ import annotations

import hashlib
import logging

from firebase_functions import scheduler_fn
from firebase_functions.options import MemoryOption, Timezone, set_global_options
from firebase_functions.params import IntParam, PROJECT_ID, SecretParam, StringParam
from google.cloud import secretmanager

from sync_service import run_sync


# Set this to a service account in the Firebase project before deploying.
RUNTIME_SERVICE_ACCOUNT = "garmin-sheets-sync@YOUR_PROJECT_ID.iam.gserviceaccount.com"
GARMIN_TOKEN = SecretParam("GARMIN_TOKEN")
GARMIN_SHEET_ID = StringParam(
    "GARMIN_SHEET_ID",
    description="Spreadsheet ID from the Garmin Health Live Google Sheets URL",
)
GARMIN_BACKFILL_START = StringParam(
    "GARMIN_BACKFILL_START",
    default="2024-01-01",
    description="First date to retrieve from Garmin, formatted YYYY-MM-DD",
)
GARMIN_BACKFILL_BATCH_DAYS = IntParam(
    "GARMIN_BACKFILL_BATCH_DAYS",
    default=7,
    description="Historical days processed per scheduled execution",
)
GARMIN_REFRESH_DAYS = IntParam(
    "GARMIN_REFRESH_DAYS",
    default=3,
    description="Recent days rechecked because Garmin may revise their values",
)

set_global_options(region="asia-south1", max_instances=1)


def _secret_name() -> str:
    return f"projects/{PROJECT_ID.value}/secrets/GARMIN_TOKEN"


def _read_latest_token() -> str:
    """Read Secret Manager latest so a warm function never uses a stale token."""
    try:
        response = secretmanager.SecretManagerServiceClient().access_secret_version(
            request={"name": f"{_secret_name()}/versions/latest"}
        )
        return response.payload.data.decode("utf-8")
    except Exception:
        logging.exception("Could not read GARMIN_TOKEN latest; falling back to the bound secret")
        return GARMIN_TOKEN.value


def _persist_token_if_changed(old_token: str, new_token: str) -> bool:
    if hashlib.sha256(old_token.encode()).digest() == hashlib.sha256(new_token.encode()).digest():
        return False
    secretmanager.SecretManagerServiceClient().add_secret_version(
        request={
            "parent": _secret_name(),
            "payload": {"data": new_token.encode("utf-8")},
        }
    )
    return True


@scheduler_fn.on_schedule(
    schedule="0 11,21 * * *",
    timezone=Timezone("Asia/Kolkata"),
    region="asia-south1",
    memory=MemoryOption.GB_1,
    timeout_sec=900,
    max_instances=1,
    concurrency=1,
    cpu="gcf_gen1",
    service_account=RUNTIME_SERVICE_ACCOUNT,
    retry_count=1,
    min_backoff_seconds=60,
    secrets=[GARMIN_TOKEN],
)
def sync_garmin_to_sheets(event: scheduler_fn.ScheduledEvent) -> None:
    token = _read_latest_token()
    result = run_sync(
        spreadsheet_id=GARMIN_SHEET_ID.value,
        token_json=token,
        backfill_start=GARMIN_BACKFILL_START.value,
        backfill_batch_days=GARMIN_BACKFILL_BATCH_DAYS.value,
        refresh_days=GARMIN_REFRESH_DAYS.value,
        trigger=event.job_name or "scheduler",
        token_persister=_persist_token_if_changed,
    )
    logging.info("Garmin sync result: %s", result)
