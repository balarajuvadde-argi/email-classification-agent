from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Protocol

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from .gmail_client import GMAIL_MODIFY_SCOPE


class TokenCipher(Protocol):
    def encrypt(self, user_id: str, plaintext: bytes) -> str: ...

    def decrypt(self, user_id: str, ciphertext: str) -> bytes: ...


class KmsTokenCipher:
    """Envelope boundary for per-user OAuth grants stored in DynamoDB."""

    def __init__(self, key_id: str, *, region_name: str | None = None, client: Any = None) -> None:
        if not key_id:
            raise ValueError("A KMS key ID is required")
        if client is None:
            import boto3

            client = boto3.client("kms", region_name=region_name)
        self._client = client
        self._key_id = key_id

    @staticmethod
    def _context(user_id: str) -> dict[str, str]:
        # KMS encryption context is recorded in plaintext in CloudTrail. Bind the
        # ciphertext to a stable opaque reference instead of the raw Google subject.
        user_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return {"application": "universal-email-classifier", "user_ref": user_ref}

    def encrypt(self, user_id: str, plaintext: bytes) -> str:
        response = self._client.encrypt(
            KeyId=self._key_id,
            Plaintext=plaintext,
            EncryptionContext=self._context(user_id),
        )
        return base64.b64encode(response["CiphertextBlob"]).decode("ascii")

    def decrypt(self, user_id: str, ciphertext: str) -> bytes:
        response = self._client.decrypt(
            CiphertextBlob=base64.b64decode(ciphertext.encode("ascii"), validate=True),
            EncryptionContext=self._context(user_id),
        )
        return bytes(response["Plaintext"])


class FernetTokenCipher:
    """Local-development cipher. Production configuration rejects this mode."""

    def __init__(self, key: str | bytes) -> None:
        self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)

    def encrypt(self, user_id: str, plaintext: bytes) -> str:
        del user_id
        return self._fernet.encrypt(plaintext).decode("ascii")

    def decrypt(self, user_id: str, ciphertext: str) -> bytes:
        del user_id
        return self._fernet.decrypt(ciphertext.encode("ascii"))


def credentials_to_grant(credentials: Credentials) -> bytes:
    payload = {
        "refresh_token": credentials.refresh_token,
        # Identity scopes are needed only to bind the OAuth callback. Persist the
        # narrow Gmail refresh scope, not every scope a Google client may return.
        "scopes": [GMAIL_MODIFY_SCOPE],
    }
    if not payload["refresh_token"]:
        raise RuntimeError("Google did not return an offline refresh token")
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def credentials_from_grant(grant: bytes, client_config: dict[str, Any]) -> Credentials:
    data = json.loads(grant.decode("utf-8"))
    web = client_config.get("web") or client_config.get("installed") or {}
    if not web.get("client_id") or not web.get("client_secret"):
        raise RuntimeError("Google OAuth client configuration is incomplete")
    return Credentials(
        token=None,
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=web["client_id"],
        client_secret=web["client_secret"],
        scopes=list(data.get("scopes") or []),
    )
