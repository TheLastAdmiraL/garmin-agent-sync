# Repository guidance for coding agents

- Read `README.md` and the relevant files in `docs/` before editing deployment, authentication, Sheets schema, or Garmin collection behavior.
- Treat Garmin passwords, serialized Garmin sessions, Google OAuth data, service-account files, workbook IDs, personal health data, and production logs as sensitive. Never ask the user to paste them into chat, read them back, or include them in tool output.
- Use only synthetic fixtures and examples. Do not read files under `garmin/`, print credential files, or upload real Garmin data.
- Preserve the read-only Garmin behavior, stable IDs, idempotent reruns, bounded retries, single-run guard, and checkpoint-after-write ordering.
- Do not create Google Cloud resources, change IAM, expose a service, or deploy without explicit user authorization. Before an authorized deployment, summarize exact resources/commands, rollback, and teardown.
- Keep provider-specific SDKs and triggers in thin wrappers. Do not claim a provider is supported until its guide has been exercised.
- Do not silently rewrite workbook headers or advance a checkpoint after an incomplete write. Explain a migration and backup plan first.
- Use redacted logs and report which commands were actually run. Do not claim checks passed when they were not run.

Start from [the project README](README.md), [architecture](docs/architecture.md), and [the agent setup guide](docs/agent-setup.md).
