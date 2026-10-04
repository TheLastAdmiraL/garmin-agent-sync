# Garmin authentication and sessions

## Login behavior

The project uses the `garminconnect` Python client to authenticate to Garmin Connect personal-account services. The library can use a password login and prompt for MFA/challenge input, then serialize reusable session data. This is an unofficial integration with private endpoints; Garmin may invalidate sessions, require additional challenges, throttle requests, or change behavior without notice.

Run `python functions/local_run.py login` in a trusted interactive terminal. Enter the account email, password, and any MFA code there. The password is not stored. If Garmin rejects the saved session, run `reauth` interactively and, for Firebase, update the Secret Manager secret using the deployment guide.

An agent must never request, read, print, summarize, or paste the Garmin password or serialized session. Let the user type secrets directly into the hidden terminal prompt or approved secret manager.

## Storage by host

- **Windows:** local runner reuses the repository's DPAPI-protected session store, tied to the current Windows user and machine protection state.
- **Linux:** local runner stores the serialized session at `$XDG_STATE_HOME/garmin-agent-sync/garmin-token.json`, or `~/.local/state/garmin-agent-sync/garmin-token.json`, with directory mode `0700` and file mode `0600`. It is not encrypted by this project; rely on disk encryption and protect the service account/home directory.
- **Firebase:** the session is in Secret Manager. The function reads the latest version and appends a new version when Garmin refreshes it. Configure the runtime service account with only the required secret access and version-add permission.
- **Docker:** mount the persistent state volume only for the sync service user. The local runner uses the Linux state-file behavior inside the container.

## Rotation, backup, and deletion

1. Reauthenticate with `functions/local_run.py reauth` on the host that owns the session.
2. For Firebase, run `python scripts/set_cloud_token.py --project YOUR_PROJECT_ID` from a trusted terminal after the local login. The helper pipes the session to Firebase CLI stdin and does not print it.
3. Keep session backups only in an encrypted, access-controlled backup. Do not copy them into Git, a shared drive, issue reports, or agent transcripts.
4. Deleting the local file or secret version removes that stored copy; it may not invalidate an already issued Garmin session. Use Garmin's account security/session controls where available and reauthenticate if access must be rotated.
5. For secure deletion, follow the storage medium's erase/encryption-key destruction procedure. Ordinary file deletion may not erase flash or cloud backups immediately.

For application-managed copies, remove `%LOCALAPPDATA%\GarminCodex\garmin-token.bin` on Windows or `$XDG_STATE_HOME/garmin-agent-sync/garmin-token.json` (default `~/.local/state/garmin-agent-sync/garmin-token.json`) on Linux. For Firebase, disable the function before deleting all `GARMIN_TOKEN` secret versions. These steps delete this project's copies; they do not prove Garmin invalidated the session.

## Moving to another host

Do not copy a Windows DPAPI token to a different Windows user or machine; run `reauth` there. A Linux session file can be migrated only over an encrypted channel to a host you control; preserve restrictive ownership/mode and verify with `doctor`. For Firebase, provision the token through `scripts/set_cloud_token.py` from a trusted host, then delete any temporary transfer copy.
