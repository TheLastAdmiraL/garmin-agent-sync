# Linux server deployment

Use this option for a private 24/7 Linux host. The runner writes the session under a user-only state directory and uses the same Sheets sync engine as Firebase. Choose either direct systemd execution or the optional Docker Compose wrapper; do not schedule both against the same workbook.

## Direct Python + systemd

1. Install Python 3.12+ and `venv`. Create a dedicated non-login user and credential directory:

   ```bash
   sudo useradd --system --create-home --home-dir /var/lib/garmin-agent-sync --shell /usr/sbin/nologin garmin-sync
   sudo install -d -o root -g garmin-sync -m 0750 /opt/garmin-agent-sync
   sudo install -d -o garmin-sync -g garmin-sync -m 0750 /opt/garmin-agent-sync-venv
   sudo install -d -o root -g garmin-sync -m 0750 /etc/garmin-agent-sync
   ```

2. Clone or copy your chosen repository into `/opt/garmin-agent-sync`; make source readable by `garmin-sync` and writable only by the deployment/admin account:

   ```bash
   sudo chown -R root:garmin-sync /opt/garmin-agent-sync
   sudo chmod -R g+rX,o-rwx /opt/garmin-agent-sync
   ```

3. Install the local runner dependencies into a separate virtual environment:

   ```bash
   cd /opt/garmin-agent-sync
   sudo -u garmin-sync python3.12 -m venv /opt/garmin-agent-sync-venv
   sudo -u garmin-sync /opt/garmin-agent-sync-venv/bin/python -m pip install --upgrade pip
   sudo -u garmin-sync /opt/garmin-agent-sync-venv/bin/python -m pip install -r /opt/garmin-agent-sync/functions/requirements-local.txt
   sudo install -o garmin-sync -g garmin-sync -m 0600 /opt/garmin-agent-sync/.env.example /opt/garmin-agent-sync/.env
   ```

4. Edit `.env` with the spreadsheet ID and a backfill start date. Do not put credential JSON or token values in it.
5. Configure Google ADC. Prefer workload identity/attached identity if this host runs in Google Cloud. Otherwise set up an appropriate ADC identity or a dedicated service-account credential file at a protected path such as `/etc/garmin-agent-sync/google.json`; set file owner to `garmin-sync` and mode `0600`. Put only that path in `.env` as `GOOGLE_APPLICATION_CREDENTIALS=/etc/garmin-agent-sync/google.json`, then share only the target sheet with its service-account email as Editor.
6. As `garmin-sync`, run the login interactively with `XDG_STATE_HOME=/var/lib`, then `doctor`, `sync --dry-run`, and one `sync`. For example:

   ```bash
   sudo -u garmin-sync -H env XDG_STATE_HOME=/var/lib /opt/garmin-agent-sync-venv/bin/python /opt/garmin-agent-sync/functions/local_run.py login
   sudo -u garmin-sync -H env XDG_STATE_HOME=/var/lib /opt/garmin-agent-sync-venv/bin/python /opt/garmin-agent-sync/functions/local_run.py doctor
   sudo -u garmin-sync -H env XDG_STATE_HOME=/var/lib /opt/garmin-agent-sync-venv/bin/python /opt/garmin-agent-sync/functions/local_run.py sync --dry-run
   sudo -u garmin-sync -H env XDG_STATE_HOME=/var/lib /opt/garmin-agent-sync-venv/bin/python /opt/garmin-agent-sync/functions/local_run.py sync
   ```

   Confirm the state file is under `/var/lib/garmin-agent-sync/garmin-token.json` and has mode `0600`.
7. Install `infra/systemd/garmin-agent-sync.service` and `.timer`, review all paths and schedule, then run:

   ```bash
   sudo install -o root -g root -m 0644 infra/systemd/garmin-agent-sync.service /etc/systemd/system/
   sudo install -o root -g root -m 0644 infra/systemd/garmin-agent-sync.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now garmin-agent-sync.timer
   systemctl list-timers garmin-agent-sync.timer
   journalctl -u garmin-agent-sync.service
   ```

The included unit runs at 11:00 and 21:00 Asia/Kolkata with persistent catch-up after downtime. Change those times to your needs. Systemd's kernel-managed lock releases on process exit; credentials and checkpoint survive reboot in `/var/lib` and the workbook.

## Network and operations

Allow outbound HTTPS to Garmin Connect and Google APIs. Keep NTP/clock synchronization enabled. The service writes to systemd-journald rather than a project log file; check it with `journalctl -u garmin-agent-sync.service` and set journald retention according to your host's log policy. Monitor disk space, state-file permissions, and host health. Back up Google credential files and `.env` encrypted; back up or export the Sheet. The Garmin session file is not encrypted by this project, so protect the disk and `/var/lib/garmin-agent-sync` with host access controls and disk encryption.

After a Garmin authentication failure, stop the timer, reauthenticate as `garmin-sync` using `reauth`, run `doctor` and a dry run, then resume. For upgrades, stop the timer, update source/dependencies, run checks, perform one manual sync, and resume. See [Docker server](docker-server.md) for a container wrapper using the same runner and a persistent volume.
