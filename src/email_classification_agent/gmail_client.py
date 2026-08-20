from __future__ import annotations

import json
from typing import Any

from .config import Settings, secret_json

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


class GmailClient:
    """Small Gmail API wrapper that exposes label-only writes.

    This class intentionally has no archive, trash, delete, or send methods.
    The only message write is add_labels(), which always sends an empty
    removeLabelIds list so the INBOX label is preserved.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self._label_cache: list[dict[str, Any]] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> GmailClient:
        # Imported lazily so policy/unit tests can run without Google SDK packages.
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        credentials = _credentials_from_settings(settings)
        if not credentials.valid and credentials.refresh_token:
            credentials.refresh(Request())
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service)

    @classmethod
    def from_credentials(cls, credentials: Any) -> GmailClient:
        """Build a client from one user's OAuth credentials.

        The multi-tenant web application obtains credentials from its encrypted
        per-user grant store. Keeping this constructor separate prevents a tenant
        request from falling back to the legacy process-wide token settings.
        """
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not credentials.valid and credentials.refresh_token:
            credentials.refresh(Request())
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service)

    def profile_email(self) -> str:
        profile = self._service.users().getProfile(userId="me").execute()
        return str(profile.get("emailAddress") or "").lower()

    def list_labels(self) -> list[dict[str, Any]]:
        response = self._service.users().labels().list(userId="me").execute()
        labels = list(response.get("labels") or [])
        self._label_cache = labels
        return labels

    def find_label_id(self, name: str, *, user_only: bool = False) -> str | None:
        labels = self._label_cache if self._label_cache is not None else self.list_labels()
        for label in labels:
            if (
                str(label.get("name") or "").casefold() == name.casefold()
                and (not user_only or str(label.get("type") or "").casefold() == "user")
            ):
                return str(label.get("id") or "") or None
        return None

    def ensure_label(self, name: str, visible: bool, create: bool = True) -> str:
        existing = self.find_label_id(name, user_only=True)
        if existing:
            return existing
        if not create:
            return f"DRYRUN:{name}"
        body = {
            "name": name,
            "messageListVisibility": "show" if visible else "hide",
            "labelListVisibility": "labelShow" if visible else "labelHide",
        }
        created = self._service.users().labels().create(userId="me", body=body).execute()
        label_id = str(created.get("id") or "")
        if not label_id:
            raise RuntimeError(f"Gmail created label {name!r} without returning an ID")
        if self._label_cache is not None:
            self._label_cache.append({"id": label_id, "name": name, "type": "user"})
        return label_id

    def list_candidate_message_ids(
        self,
        base_query: str,
        processed_label_name: str,
        max_results: int | None,
    ) -> list[str]:
        query = f'{base_query} -label:"{processed_label_name}"'.strip()
        ids: list[str] = []
        page_token: str | None = None
        while True:
            remaining = None if max_results is None else max_results - len(ids)
            if remaining is not None and remaining <= 0:
                break
            request_size = min(500, remaining) if remaining is not None else 500
            response = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    labelIds=["INBOX"],
                    includeSpamTrash=False,
                    maxResults=request_size,
                    pageToken=page_token,
                )
                .execute()
            )
            ids.extend(str(item["id"]) for item in (response.get("messages") or []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return ids

    def list_message_ids(self, query: str, max_results: int) -> list[str]:
        """List a bounded set of Inbox message IDs for a tenant-owned query."""
        if max_results < 1 or max_results > 500:
            raise ValueError("max_results must be between 1 and 500")
        response = (
            self._service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                labelIds=["INBOX"],
                includeSpamTrash=False,
                maxResults=max_results,
            )
            .execute()
        )
        return [str(item["id"]) for item in (response.get("messages") or [])]

    def get_message(self, message_id: str) -> dict[str, Any]:
        resource = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        self._hydrate_text_part_bodies(resource)
        return resource

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        for message in thread.get("messages") or []:
            self._hydrate_text_part_bodies(message)
        return thread

    def _hydrate_text_part_bodies(self, resource: dict[str, Any]) -> None:
        """Load text MIME bodies that Gmail returned via attachmentId.

        Gmail may place a large text/plain or text/html body in the attachments
        resource instead of inline `body.data`. Only unnamed text parts are fetched;
        binary/file attachments and attached .eml training samples are never read by
        this method.
        """
        message_id = str(resource.get("id") or "")
        payload = resource.get("payload") or {}
        if not message_id or not payload:
            return

        stack = [payload]
        while stack:
            part = stack.pop()
            stack.extend(part.get("parts") or [])
            mime_type = str(part.get("mimeType") or "").lower()
            filename = str(part.get("filename") or "")
            body = part.get("body") or {}
            if filename or mime_type not in {"text/plain", "text/html"}:
                continue
            if body.get("data") or not body.get("attachmentId"):
                continue
            attachment = (
                self._service.users()
                .messages()
                .attachments()
                .get(
                    userId="me",
                    messageId=message_id,
                    id=str(body["attachmentId"]),
                )
                .execute()
            )
            data = attachment.get("data")
            if data:
                body["data"] = data
                part["body"] = body

    def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        clean_ids = [label_id for label_id in label_ids if not label_id.startswith("DRYRUN:")]
        if not clean_ids:
            return
        body = {"addLabelIds": clean_ids, "removeLabelIds": []}
        (
            self._service.users()
            .messages()
            .modify(userId="me", id=message_id, body=body)
            .execute()
        )


def _credentials_from_settings(settings: Settings) -> Any:
    # Imported lazily so policy/unit tests can run without Google SDK packages.
    from google.oauth2.credentials import Credentials

    scopes = [GMAIL_MODIFY_SCOPE]
    if settings.gmail_oauth_secret_id:
        data = secret_json(settings.gmail_oauth_secret_id, settings.aws_region)
        return _credentials_from_mapping(data, scopes)
    if settings.gmail_credentials_json:
        data = json.loads(settings.gmail_credentials_json)
        if not isinstance(data, dict):
            raise RuntimeError("GMAIL_CREDENTIALS_JSON must be a JSON object")
        return _credentials_from_mapping(data, scopes)
    if settings.gmail_token_file:
        if not settings.gmail_token_file.exists():
            raise FileNotFoundError(f"Gmail token file not found: {settings.gmail_token_file}")
        return Credentials.from_authorized_user_file(str(settings.gmail_token_file), scopes=scopes)
    raise RuntimeError(
        "No Gmail OAuth credentials configured. Set GMAIL_TOKEN_FILE, "
        "GMAIL_CREDENTIALS_JSON, or GMAIL_OAUTH_SECRET_ID."
    )


def _credentials_from_mapping(data: dict[str, Any], scopes: list[str]) -> Any:
    # Imported lazily so policy/unit tests can run without Google SDK packages.
    from google.oauth2.credentials import Credentials

    # Accept either Google authorized-user JSON or a compact Secrets Manager object.
    if data.get("type") == "authorized_user":
        return Credentials.from_authorized_user_info(data, scopes=scopes)
    required = ("client_id", "client_secret", "refresh_token")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise RuntimeError(f"Gmail OAuth secret is missing: {', '.join(missing)}")
    return Credentials(
        token=data.get("token"),
        refresh_token=str(data["refresh_token"]),
        token_uri=str(data.get("token_uri") or "https://oauth2.googleapis.com/token"),
        client_id=str(data["client_id"]),
        client_secret=str(data["client_secret"]),
        scopes=scopes,
    )
