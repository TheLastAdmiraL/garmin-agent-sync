# Docker Compose on a Linux server

Docker is a packaging option for the Linux local runner. Scheduling remains on the host through systemd so the container exits after each run. Use either this schedule or the direct systemd service in [the Linux guide](linux-server.md), not both.

## Prepare files and authenticate

From the repository root, create `.env` from `.env.example` and fill in the Sheet ID and backfill date. Set `SYNC_UID` and `SYNC_GID` to the numeric UID/GID of the Linux sync user (`id -u garmin-sync` and `id -g garmin-sync`). Keep a service-account credential file outside the repository, owned by that user with mode `0600`. Share only the target workbook with that service-account email. Set `GOOGLE_SHEETS_CREDENTIALS` in the shell to the credential file's host path before using Compose, for example `export GOOGLE_SHEETS_CREDENTIALS=/etc/garmin-agent-sync/google.json`.

Build the image, then create the Garmin session interactively in the same persistent volume used by later runs:

```bash
docker compose build
docker compose run --rm -it sync login
docker compose run --rm sync doctor
docker compose run --rm sync sync --dry-run
docker compose run --rm sync sync
```

The Compose file mounts `.env` read-only, the Google credential file read-only, and a persistent named volume at `/data`. The Linux Garmin session is a file in that volume with mode `0600`. The container runs as a non-root user.

## Schedule it

Install the included host systemd service and timer after reviewing their paths and schedule:

```bash
sudo install -o root -g root -m 0644 infra/systemd/garmin-agent-sync-docker.service /etc/systemd/system/
sudo install -o root -g root -m 0644 infra/systemd/garmin-agent-sync-docker.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garmin-agent-sync-docker.timer
systemctl list-timers garmin-agent-sync-docker.timer
journalctl -u garmin-agent-sync-docker.service
```

The unit expects `/opt/garmin-agent-sync`, `/etc/garmin-agent-sync/google.json`, Docker Compose, and `.env` at the repository root. It runs as `garmin-sync`; grant that user access to the Docker socket and credential file only if you accept the host-control implications. Docker socket access can grant broad host control; prefer rootless Docker or a dedicated trusted service identity. The local runner's OS lock prevents concurrent syncs using the same named volume. Container output goes to the Docker logging driver; configure host-side log rotation/size limits. Follow [the Linux server guide](linux-server.md) for host checks. Keep the volume in backups; do not run `docker compose down --volumes` unless you intend to delete its Garmin session.
