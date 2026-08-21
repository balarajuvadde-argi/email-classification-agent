from __future__ import annotations

import os
import secrets
from typing import Any
from urllib.parse import urlsplit

from google_auth_oauthlib.flow import Flow

from .gmail_client import GMAIL_MODIFY_SCOPE

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

GOOGLE_IDENTITY_SCOPES = ("openid", "email")


class GoogleOAuthManager:
    def __init__(self, client_config: dict[str, Any], redirect_uri: str) -> None:
        web = client_config.get("web")
        if not isinstance(web, dict):
            raise RuntimeError("Production OAuth requires a Google Web application client")
        if not web.get("client_id") or not web.get("client_secret"):
            raise RuntimeError("Google Web OAuth client ID and secret are required")
        if not str(web["client_id"]).endswith(".apps.googleusercontent.com"):
            raise RuntimeError("Google OAuth client ID has an unexpected issuer")
        if web.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth":
            raise RuntimeError("Google OAuth authorization endpoint is not trusted")
        if web.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise RuntimeError("Google OAuth token endpoint is not trusted")
        parsed = urlsplit(redirect_uri)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.scheme != "https" and not loopback)
        ):
            raise RuntimeError("OAuth redirect URI must use HTTPS outside localhost")
        if redirect_uri not in set(web.get("redirect_uris") or []):
            raise RuntimeError("OAuth redirect URI is not registered in the client configuration")
        self.client_config = client_config
        self.redirect_uri = redirect_uri

    def authorization_url(
        self,
        state: str,
        *,
        gmail_access: bool,
    ) -> tuple[str, str, str]:
        code_verifier = secrets.token_urlsafe(96)[:128]
        id_token_nonce = secrets.token_urlsafe(32)
        scopes = [*GOOGLE_IDENTITY_SCOPES]
        if gmail_access:
            scopes.append(GMAIL_MODIFY_SCOPE)
        flow = Flow.from_client_config(
            self.client_config,
            scopes=scopes,
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self.redirect_uri
        options = {
            "access_type": "offline" if gmail_access else "online",
            "include_granted_scopes": "false",
            "prompt": "consent select_account" if gmail_access else "select_account",
            "nonce": id_token_nonce,
        }
        url, returned_state = flow.authorization_url(**options)
        if returned_state != state:
            raise RuntimeError("OAuth library changed the supplied state value")
        return url, code_verifier, id_token_nonce

    def fetch_credentials(
        self,
        *,
        state: str,
        code_verifier: str,
        authorization_response: str,
        gmail_access: bool,
    ) -> Any:
        scopes = [*GOOGLE_IDENTITY_SCOPES]
        if gmail_access:
            scopes.append(GMAIL_MODIFY_SCOPE)
        flow = Flow.from_client_config(
            self.client_config,
            scopes=scopes,
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self.redirect_uri
        flow.fetch_token(authorization_response=authorization_response)
        return flow.credentials
