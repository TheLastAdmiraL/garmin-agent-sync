# Threat model

## Scope and assumptions

The project runs for one Garmin account and one Google spreadsheet per installation. The user controls the host, Google project, account, and workbook. The code does not host a shared service. Garmin personal-account endpoints are unofficial and may change.

## Main threats and mitigations

| Threat | Mitigation | Remaining exposure |
| --- | --- | --- |
| Garmin session or Google credential theft from a host | Windows uses DPAPI; Linux state files use `0700`/`0600` and should live on an encrypted disk; Firebase uses Secret Manager and an attached service identity; credential files stay outside Git | Host administrators, malware, or a compromised runtime can use credentials available to that runtime |
| Accidental workbook exposure | Keep the Sheet private, share only with a dedicated service account, avoid broad Drive scope, review agent/connector access | Anyone granted workbook or Google-account access can see the data |
| Secret or biometric data committed to a public repository | `.gitignore`, `.dockerignore`, synthetic examples, interactive secret entry, and agent instructions prohibit secrets in source/chat | Git history and backups retain anything committed before detection; rotate credentials and clean history carefully |
| Malicious or mistaken coding-agent action | Agent guide forbids reading secrets and requires explicit authorization before cloud/IAM/deploy actions; use a local review of diffs and commands | Agents with shell access can still alter code; do not give them production credentials or unrestricted cloud access |
| Partial Sheets writes or overlapping runs | Stable keys, bounded writes/retries, OS-level local lock, Firebase max instance/concurrency limits, checkpoint after data writes, and rerun recovery | The workbook is not transactional; inspect `Sync Log` and `Errors` and rerun after failures |
| Garmin endpoint or dependency changes | Pin dependencies, isolate Sheets collector adapter, record versions/licenses, run in a disposable setup before updating | Garmin may break or rate-limit private endpoints without notice |
| Sensitive data in logs or errors | Avoid payload logging and IDs, redact common credential formats, retain only useful operational error summaries | Upstream exception text may contain unexpected sensitive material; review logs before sharing reports |

## Out of scope

This project does not guarantee Garmin API availability, protect a compromised host, encrypt the SQLite archive itself, prevent Google account compromise, or decide which training/health recommendations an agent should make. It is not medical software.
