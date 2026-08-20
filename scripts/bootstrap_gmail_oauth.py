#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a Gmail OAuth refresh-token JSON for the classification agent"
    )
    parser.add_argument("--client-secret", required=True, type=Path)
    parser.add_argument("--output", default=Path("gmail_oauth_secret.json"), type=Path)
    parser.add_argument("--expected-account", default="")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), scopes=[SCOPE])
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke the app grant and rerun with prompt=consent."
        )

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    mailbox = str(profile.get("emailAddress") or "").lower()
    if args.expected_account and mailbox.casefold() != args.expected_account.casefold():
        raise RuntimeError(
            f"Authenticated the wrong Gmail account: {mailbox!r}; expected {args.expected_account!r}"
        )

    payload = {
        "type": "authorized_user",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "scopes": [SCOPE],
    }
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(f"Verified mailbox: {mailbox}")
    print(f"Wrote OAuth secret JSON to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
