#!/usr/bin/env python3
"""Copy the local Garmin session into Firebase Secret Manager.

The token is sent to the Firebase CLI over stdin and is never printed or saved
inside this repository.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions"))

from local_run import load_session  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Set GARMIN_TOKEN from the encrypted local session")
    parser.add_argument("--project", required=True, help="Firebase project ID to receive the new secret version")
    args = parser.parse_args()
    command = [
        "npx.cmd" if sys.platform == "win32" else "npx",
        "-y",
        "firebase-tools@latest",
        "functions:secrets:set",
        "GARMIN_TOKEN",
        "--project",
        args.project,
    ]
    subprocess.run(command, cwd=ROOT, input=load_session() + "\n", text=True, check=True)
    print("GARMIN_TOKEN was set without printing the token. Deploy the function next.")


if __name__ == "__main__":
    main()
