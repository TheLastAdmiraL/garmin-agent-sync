# ADR 0002: Shared Sheets engine with host-specific wrappers

- **Status:** Adopted for this reference project.
- **Date:** 2026-10-04

## Context

Users need a clear example that can run on a Windows server, a Linux server, or a cloud function. The existing Firebase Functions implementation already syncs Garmin records into Google Sheets and is the reference deployment. Host-specific secrets, schedules, and cloud SDKs should not force users to maintain different versions of the data mapping and sync rules.

## Decision

Keep the Sheets sync behavior in `functions/cloud_sync.py`, `functions/sync_service.py`, and `functions/sheets_store.py`. Provide thin wrappers: `functions/local_run.py` for Windows/Linux and `functions/main.py` for Firebase. Document other providers with a checklist until their adapter is implemented and exercised. Keep the local SQLite archive as an optional, separate companion because it has a different purpose, state model, and Windows DPAPI authentication path.

## Consequences

- Windows/Linux and Firebase share Garmin collection, schema, retry, and checkpoint behavior.
- Local session storage and Google credentials differ by host and must be documented independently.
- The workbook is the sync's durable state store; a multi-tab Sheets run is not transactional, so stable keys and reruns must repair partial writes.
- The SQLite archive is not automatically exported to Sheets and must not be presented as the local implementation of the Sheets sync.
- Provider support claims require a tested deployment recipe and a maintainer.
