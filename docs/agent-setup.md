# Agent-assisted setup and changes

This guide is tool-agnostic. It can be used with Codex, Claude Code, Gemini CLI, or another coding agent. Keep secrets in a trusted terminal, operating-system credential store, or Google Secret Manager; do not put them in the agent conversation.

## Setup with an agent

1. Ask the agent to read `README.md`, this guide, and the deployment guide for your chosen target.
2. Have it identify your operating system and ask which supported option you chose: Windows server, Linux server, Firebase Functions, or a documented provider adapter.
3. Let it explain the architecture and configuration names. Provide non-secret choices such as region, schedule/time zone, spreadsheet ID, backfill date, and host paths through your normal trusted interface.
4. Enter Garmin login/MFA only into `python functions/local_run.py login` in your own interactive terminal. Enter Google credentials into the operating system's ADC flow or place the key file at a protected path. Do not ask the agent to inspect the secret file or print its values.
5. Ask it to run `doctor`, `sync --dry-run`, and any checks in the selected guide. Share only redacted errors and counts.
6. Before cloud setup or deployment, require a written list of exact files, commands, Google resources, IAM changes, costs, rollback, and teardown. Approve the deployment action explicitly.
7. After deployment, inspect one run, a rerun, checkpoint persistence, scheduled execution, error status, and logs. Keep the backup, reauthentication, revocation, and teardown instructions.

## Asking for code changes

Give the agent a focused change request, for example: “Add Garmin's training-load metric to the daily raw collection and summary sheet for the Firebase deployment; preserve existing columns and include a migration note.” Ask it to:

- identify the adapter, mapping, persistence, tests, and docs affected before editing;
- use synthetic payloads, never production records;
- preserve row keys, retry behavior, workbook state ordering, and the selected deployment boundary;
- update configuration/schema/deployment docs with code changes;
- report checks run and any unverified deployment steps.

For manual backfill recovery, ask for a plan before editing workbook state. Export/backup the workbook first; changing a cursor can cause duplicate requests or skipped history.
