# ADR 0001: Garmin dependency and repository ownership

- **Status:** Keep the existing Garmin client behind the cloud collector; complete repository license selection before public release.
- **Date:** 2026-10-04

## Context

Garmin personal accounts do not have a documented public API that guarantees this data set. Reimplementing login, session refresh, and the changing private Garmin Connect protocol would be fragile. The working cloud path uses `garminconnect==0.3.17`; the optional Windows archive currently pins `garminconnect==0.3.11` through its independent requirements/lock.

## Decision

Continue using `garminconnect` only for Garmin protocol details. The Sheets implementation keeps calls behind `GarminCollector` in `functions/cloud_sync.py`; provider triggers, token persistence, sync state, retries, schema mapping, and Sheets writes stay in this repository. Do not vendor/copy the upstream library. Keep the exact versions pinned and review version changes with API compatibility checks.

The upstream project and the published `0.3.17` package identify the dependency as MIT licensed. The PyPI record lists one maintainer and Python 3.12+ support for that release. Its code reaches unofficial personal-account endpoints; neither its license nor active package releases imply a Garmin support guarantee. See [upstream source](https://github.com/cyberjunky/python-garminconnect) and [the pinned PyPI release metadata](https://pypi.org/project/garminconnect/0.3.17/).

Direct runtime dependencies currently pinned by this repository:

| Environment | Package | Version | License | Responsibility |
| --- | --- | --- | --- | --- |
| Optional Windows archive | [`garminconnect`](https://pypi.org/project/garminconnect/0.3.11/) | `0.3.11` | MIT | Garmin protocol client |
| Local Sheets runner and Firebase | [`garminconnect`](https://pypi.org/project/garminconnect/0.3.17/) | `0.3.17` | MIT | Garmin protocol client |
| Local Sheets runner and Firebase | [`google-api-python-client`](https://pypi.org/project/google-api-python-client/2.201.0/) | `2.201.0` | Apache-2.0 | Google Sheets API client; upstream describes this package as maintenance mode |
| Firebase only | [`firebase-functions`](https://pypi.org/project/firebase-functions/0.6.0/) | `0.6.0` | Apache-2.0 | Firebase runtime/scheduler adapter |
| Firebase only | [`google-cloud-secret-manager`](https://pypi.org/project/google-cloud-secret-manager/2.31.0/) | `2.31.0` | Apache-2.0 | Garmin session secret read/rotation |
| Local runner and Firebase | [`tzdata`](https://pypi.org/project/tzdata/2026.4/) | `2026.4` | Apache-2.0 | IANA time zone data fallback |

The local archive's `requirements.lock` is a Windows x64/Python 3.12 hash lock and includes transitive packages. Review those transitive notices when refreshing that lock. Package metadata is published by the upstream projects on [PyPI](https://pypi.org/) for the pinned releases.

The code in this repository is kept as source rather than adopting upstream implementation code. Confirm authorship and audit all copied material before choosing a repository license. No project license is set by this ADR.

## Consequences

- Garmin endpoint changes may require adapter updates and a new pinned package version.
- The local archive and cloud sync currently use different package versions and collection implementations. Consolidate only after preserving archive import/sync behavior and verifying both deployment paths.
- Public users must be told this is unofficial and may break or be throttled without notice.
- Before publishing a release, the maintainer must select a repository license, review direct and transitive dependency notices, and retain upstream attribution where required.
