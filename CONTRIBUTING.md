# Contributing

Read the [README](README.md), [architecture](docs/architecture.md), [privacy guide](docs/data-flow-and-privacy.md), and [agent setup guide](docs/agent-setup.md) first.

## Changes

- Keep Garmin calls read-only and isolated behind the collector where possible.
- Use synthetic payloads and fake spreadsheet services. Never include a real Garmin export, workbook, token, or production log in a change.
- For field or schema changes, update the declared headers, mapping, code map, and migration guidance together.
- For deployment adapters, follow the [provider checklist](docs/deployment/provider-adapter-template.md) and name a maintainer.
- Describe privacy impact, failure handling, backfill behavior, and rollback in pull requests.

## Local checks

The repository includes unit tests for the SQLite archive and Sheets transformations/orchestration. From the root, install the relevant requirements and run:

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s functions/tests -v
```

These checks do not contact Garmin or Google. Do not run live syncs against production data as part of review.

The project license is pending maintainer selection; confirm it before contributing code that requires a project-wide license grant.
