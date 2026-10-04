# Security policy

This project handles Garmin sessions, Google credentials, and highly sensitive health and location data. Do not file secrets or personal records in public issues.

## Reporting

For private reports, use GitHub's **Report a vulnerability** workflow for this repository. Include affected version, impact, and safe reproduction steps without tokens, passwords, workbook contents, or identifying Garmin data. If private reporting is unavailable, contact the repository owner through their GitHub profile and do not publish details until a fix can be coordinated.

## Supported versions and security practices

No release-support policy is established yet. Until releases are published, report issues against the latest default branch. Keep credentials outside source control, rotate compromised Garmin sessions and Google credentials promptly, restrict spreadsheet sharing, and review Google Cloud IAM regularly. See [data flow and privacy](docs/data-flow-and-privacy.md) and [session handling](docs/garmin-auth-and-sessions.md).
