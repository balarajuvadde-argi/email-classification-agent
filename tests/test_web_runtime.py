import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from email_classification_agent import web_runtime
from email_classification_agent.multitenant_store import InMemoryMultiTenantStore
from email_classification_agent.token_security import FernetTokenCipher
from email_classification_agent.web_config import WebSettings
from email_classification_agent.web_runtime import WebRuntime

CLIENT_CONFIG = {
    "web": {
        "client_id": "client.apps.googleusercontent.com",
        "client_secret": "secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8000/oauth/google/callback"],
    }
}


def _settings(key):
    return WebSettings(
        environment="development",
        app_base_url="http://localhost:8000",
        table_name="",
        kms_key_id=None,
        local_token_encryption_key=key.decode(),
        google_oauth_secret_id=None,
        google_oauth_client_config_json=json.dumps(CLIENT_CONFIG),
        google_oauth_client_file=None,
        openai_api_key_secret_id=None,
        openai_api_key="test",
        openai_model="test",
        queue_url=None,
        aws_region=None,
    )


class _OAuth:
    pass


class _Gmail:
    def __init__(self, email):
        self.email = email

    def profile_email(self):
        return self.email


def _credentials():
    return Credentials(
        token="access",
        refresh_token="refresh",
        id_token="raw-id-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client.apps.googleusercontent.com",
        client_secret="secret",
        scopes=["openid", "email", "https://www.googleapis.com/auth/gmail.modify"],
    )


def test_oauth_identity_is_bound_to_gmail_profile_and_stable_subject() -> None:
    key = Fernet.generate_key()
    store = InMemoryMultiTenantStore()
    runtime = WebRuntime(
        _settings(key),
        store=store,
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "google-sub-123",
            "email": "USER@example.com",
            "email_verified": True,
        },
        gmail_factory=lambda credentials: _Gmail("user@example.com"),
        classifier_factory=lambda: None,
    )

    user = runtime.authorize_user(_credentials())

    assert user.user_id == "google-sub-123"
    assert user.email == "user@example.com"
    assert "refresh" not in user.encrypted_grant
    assert store.get_user("google-sub-123") == user
    assert user.policy is not None
    assert user.policy.labels == [
        "Acquisitions/On Market",
        "Acquisitions/Off Market",
        "Acquisitions/Wholesale",
        "News",
    ]
    assert user.policy.automatic_enabled is True
    assert user.next_run_at == 0


def test_new_google_account_can_connect_without_mailbox_allowlist() -> None:
    key = Fernet.generate_key()
    store = InMemoryMultiTenantStore()
    runtime = WebRuntime(
        _settings(key),
        store=store,
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "client-sub",
            "email": "maurice@argifamily.com",
            "email_verified": True,
        },
        gmail_factory=lambda credentials: _Gmail("maurice@argifamily.com"),
        classifier_factory=lambda: None,
    )

    user = runtime.authorize_user(_credentials())

    assert user.email == "maurice@argifamily.com"
    assert user.user_id == "client-sub"
    assert user.policy is not None
    assert "Acquisitions/On Market" in user.policy.labels
    assert "Acquisitions/Off Market" in user.policy.labels
    assert "Acquisitions/Wholesale" in user.policy.labels
    assert "News" in user.policy.labels


def test_database_url_selects_postgres_store(monkeypatch) -> None:
    created = {}

    class _Store(InMemoryMultiTenantStore):
        def __init__(self, database_url):
            super().__init__()
            created["database_url"] = database_url

    monkeypatch.setattr(web_runtime, "PostgresMultiTenantStore", _Store)
    settings = replace(
        _settings(Fernet.generate_key()),
        database_url="postgresql://user:pass@db.internal/app",
    )

    store = WebRuntime._build_store(settings)

    assert isinstance(store, _Store)
    assert created["database_url"] == "postgresql://user:pass@db.internal/app"


def test_existing_session_cannot_connect_a_different_google_subject() -> None:
    key = Fernet.generate_key()
    store = InMemoryMultiTenantStore()
    runtime = WebRuntime(
        _settings(key),
        store=store,
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "attacker-sub",
            "email": "attacker@example.com",
            "email_verified": True,
        },
        gmail_factory=lambda credentials: _Gmail("attacker@example.com"),
        classifier_factory=lambda: None,
    )

    with pytest.raises(RuntimeError, match="does not match"):
        runtime.authorize_user(_credentials(), expected_user_id="signed-in-sub")

    assert store.get_user("attacker-sub") is None


def test_id_token_nonce_is_bound_to_the_oauth_request() -> None:
    key = Fernet.generate_key()
    store = InMemoryMultiTenantStore()
    runtime = WebRuntime(
        _settings(key),
        store=store,
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "sub",
            "email": "user@example.com",
            "email_verified": True,
            "nonce": "different-nonce",
        },
        gmail_factory=lambda credentials: _Gmail("user@example.com"),
        classifier_factory=lambda: None,
    )

    with pytest.raises(RuntimeError, match="nonce"):
        runtime.authorize_user(_credentials(), expected_nonce="expected-nonce")

    assert store.get_user("sub") is None


def test_identity_email_must_match_actual_gmail_profile() -> None:
    key = Fernet.generate_key()
    runtime = WebRuntime(
        _settings(key),
        store=InMemoryMultiTenantStore(),
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "sub",
            "email": "claimed@example.com",
            "email_verified": True,
        },
        gmail_factory=lambda credentials: _Gmail("actual@example.com"),
        classifier_factory=lambda: None,
    )

    with pytest.raises(RuntimeError, match="different accounts"):
        runtime.authorize_user(_credentials())


def test_connection_rejects_unexpected_inherited_google_scope() -> None:
    key = Fernet.generate_key()
    runtime = WebRuntime(
        _settings(key),
        store=InMemoryMultiTenantStore(),
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        identity_verifier=lambda token, audience: {
            "sub": "sub",
            "email": "user@example.com",
            "email_verified": True,
        },
        gmail_factory=lambda credentials: _Gmail("user@example.com"),
        classifier_factory=lambda: None,
    )
    credentials = _credentials()
    credentials._scopes = [*(credentials.scopes or []), "https://www.googleapis.com/auth/drive"]

    with pytest.raises(RuntimeError, match="unexpected OAuth scope"):
        runtime.authorize_user(credentials)


def test_openai_404_failure_explains_model_configuration() -> None:
    key = Fernet.generate_key()
    runtime = WebRuntime(
        _settings(key),
        store=InMemoryMultiTenantStore(),
        cipher=FernetTokenCipher(key),
        oauth=_OAuth(),
        classifier_factory=lambda: None,
    )

    class MissingModelError(Exception):
        status_code = 404

    message = runtime._classification_failure_message(MissingModelError())

    assert "OPENAI_MODEL" in message
