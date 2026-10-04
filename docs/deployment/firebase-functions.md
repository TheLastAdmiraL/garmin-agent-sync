# Firebase Functions deployment

This is the deployment used by the existing project and is the reference cloud implementation in this repository. It runs the shared sync engine in Firebase Functions, uses Cloud Scheduler, stores the Garmin session in Secret Manager, and writes the user's workbook. The example is generalized; create your own Google project and service account.

## Requirements and choices

- A Firebase project on the Blaze billing plan for scheduled second-generation Functions.
- Google Cloud CLI, Node.js/npm, Firebase CLI, and Python 3.12+.
- A private Google Sheet and a dedicated runtime service account.
- Garmin session created with `python functions/local_run.py login` on Windows/Linux, or the existing local session store.
- Pick the first Garmin date needed. Backfill advances by a bounded number of days each run.

The schedule in `functions/main.py` is 11:00 and 21:00 Asia/Kolkata. The function uses a 15-minute timeout, 1 GiB memory, concurrency 1, and one maximum instance. Adjust these only after considering request volume, runtime, quota, and overlap behavior.

## 1. Create the workbook and runtime identity

Create a private spreadsheet and copy its ID from the URL. Create a dedicated service account in your own project. Set the `RUNTIME_SERVICE_ACCOUNT` constant in `functions/main.py` to its email before deployment; the repository contains a placeholder, not a maintainer identity. Share only the target spreadsheet with this service account as Editor.

The Sheets client uses the Sheets API scope. It does not require broad Drive access or make the sheet public. Enable Google Sheets API, Secret Manager API, Cloud Scheduler API, and Cloud Functions/Cloud Run build services in your project. For example:

```bash
gcloud services enable sheets.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project YOUR_PROJECT_ID
```

Create the runtime identity if needed:

```bash
gcloud iam service-accounts create garmin-sheets-sync --project YOUR_PROJECT_ID
```

Use `garmin-sheets-sync@YOUR_PROJECT_ID.iam.gserviceaccount.com` in `functions/main.py`. The person deploying the function must be permitted to deploy as this service account. Avoid project-wide roles where resource-level access is available.

## 2. Configure non-secret parameters

Create `functions/.env.YOUR_PROJECT_ID` (the name is ignored by Git) with your own values:

```dotenv
GARMIN_SHEET_ID=YOUR_SPREADSHEET_ID
GARMIN_BACKFILL_START=YYYY-MM-DD
GARMIN_BACKFILL_BATCH_DAYS=7
GARMIN_REFRESH_DAYS=3
```

Use your real earliest date instead of the placeholder. Keep these as configuration, not credentials.

## 3. Log in and provision the session secret

From a trusted terminal at the repository root, create a local Functions environment and then create/refresh a Garmin session:

```powershell
py -3.12 -m venv functions\.venv
functions\.venv\Scripts\python.exe -m pip install -r functions\requirements.txt
functions\.venv\Scripts\python.exe functions\local_run.py login
functions\.venv\Scripts\python.exe scripts\set_cloud_token.py --project YOUR_PROJECT_ID
```

The helper reads the local session and pipes it to Firebase CLI stdin. It does not echo the serialized session. Do not use terminal transcript capture or paste the token into a prompt.

The runtime identity must be able to access `GARMIN_TOKEN` and add a version when Garmin refreshes the token. Grant `roles/secretmanager.secretVersionAdder` only on that secret. Bind `GARMIN_TOKEN` as a Firebase secret when deploying; Firebase configures secret access for the function. Do not grant project-wide Secret Manager administration to the runtime identity.

After the secret exists, grant the version-adder role on the secret itself:

```bash
gcloud secrets add-iam-policy-binding GARMIN_TOKEN --project YOUR_PROJECT_ID --member="serviceAccount:garmin-sheets-sync@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/secretmanager.secretVersionAdder"
```

Verify that the deployed function identity also has secret-version access to `GARMIN_TOKEN`; the Firebase secret binding normally wires this at deploy time. Do not grant project-wide Secret Manager access.

## 4. Deploy and run

```powershell
npx -y firebase-tools@latest deploy --only functions --project YOUR_PROJECT_ID
```

Deployment creates the scheduled function and corresponding Cloud Scheduler job. Inspect the function logs and workbook tabs. To run immediately for setup/recovery, use **Run now** on that project's Cloud Scheduler job; access to that action is controlled by Google Cloud IAM. The scheduler uses the same bounded backfill and refresh behavior as the recurring invocation.

## 5. Verify and operate

1. Confirm the `README` tab ends as `SUCCESS` or `PARTIAL`, not `RUNNING`.
2. Inspect `Sync Log`, `Errors`, and `Sync State`.
3. Run the Scheduler job once more and confirm activity rows are keyed/upserted rather than duplicated.
4. Rotate by running local `reauth`, then `set_cloud_token.py` again; verify Secret Manager has the latest version.
5. Monitor runtime duration, request failure counts, workbook cell usage, and Google billing.

## Remove the deployment

Disable/delete the Cloud Scheduler job and Firebase Function, revoke secret roles and workbook sharing, delete the Garmin secret versions, and export/delete the workbook if desired. See [operations](../operations-and-troubleshooting.md) for the complete teardown and local cleanup checklist.

Firebase scheduling and scheduled-function billing are described in [Firebase's scheduled functions documentation](https://firebase.google.com/docs/functions/schedule-functions). Google documents [ADC and attached service identities](https://cloud.google.com/docs/authentication/application-default-credentials) for runtime credentials.

Use the Google Cloud Billing dashboard to review Cloud Scheduler, Functions/Cloud Run execution, Secret Manager, logging, and network charges for your project. Charges and free allowances can change; check your project's current pricing before enabling the schedule.
