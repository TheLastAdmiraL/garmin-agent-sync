# Provider adapter template

Firebase is the only cloud function deployment included here. To add another provider, keep `functions/cloud_sync.py`, `functions/sync_service.py`, and `functions/sheets_store.py` as the shared behavior. Add only a wrapper that supplies configuration, credentials, secret persistence, logs, and a trigger.

## Adapter checklist

- [ ] Invoke the shared `run_sync` entry point; do not fork collector or schema logic.
- [ ] Document the executable command and whether the adapter listens on a port; do not expose an admin/status endpoint unless authenticated and protected with TLS.
- [ ] Define each required environment variable, secret-injection mechanism, Google identity, and durable state/checkpoint behavior.
- [ ] Supply the Garmin session from the provider's secret manager and persist refreshed sessions safely.
- [ ] Supply Google Sheets credentials with access limited to the selected workbook; avoid long-lived user keys when workload identity is available.
- [ ] Provide a health/status check and durable checkpoint storage. The current engine stores its checkpoint in Sheets; document how workbook state is backed up and what happens if the workbook is unavailable.
- [ ] Configure scheduler timezone, timeout, memory, concurrency, retry policy, and a single-run guard.
- [ ] Ensure logs contain only run metadata and redacted errors, not tokens or activity payloads.
- [ ] Document APIs, IAM, secret setup/rotation, cost, manual invocation, teardown, rollback, and maintenance owner.
- [ ] Demonstrate scheduled and manual runs, partial-write rerun recovery, token rotation, and overlap handling in a disposable project.

Do not list a provider as supported until its recipe has been exercised and a maintainer is named. Read [architecture](../architecture.md) and [privacy](../data-flow-and-privacy.md) before submitting an adapter.
