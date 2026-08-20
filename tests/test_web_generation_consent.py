import json
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from email_classification_agent.multitenant_store import InMemoryMultiTenantStore
from email_classification_agent.token_security import FernetTokenCipher
from email_classification_agent.universal_models import (
    ClassificationPolicy,
    SessionRecord,
    UserRecord,
)
from email_classification_agent.web_app import create_app
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


def _settings() -> WebSettings:
    return WebSettings(
        environment="development",
        app_base_url="http://localhost:8000",
        table_name="",
        kms_key_id=None,
        local_token_encryption_key=Fernet.generate_key().decode(),
        google_oauth_secret_id=None,
        google_oauth_client_config_json=json.dumps(CLIENT_CONFIG),
        google_oauth_client_file=None,
        openai_api_key_secret_id=None,
        openai_api_key="test-key",
        openai_model="test-model",
        queue_url=None,
        aws_region=None,
    )


def _runtime(
    settings: WebSettings,
    store: InMemoryMultiTenantStore,
) -> WebRuntime:
    return WebRuntime(
        settings,
        store=store,
        cipher=FernetTokenCipher(settings.local_token_encryption_key or ""),
        oauth=object(),
        classifier_factory=lambda: None,
    )


def _policy(*, automatic: bool = False, max_messages: int = 10) -> ClassificationPolicy:
    return ClassificationPolicy(
        prompt="Label invoices and payment receipts as Finance.",
        labels=["Finance"],
        gmail_query="in:inbox",
        max_messages_per_run=max_messages,
        automatic_enabled=automatic,
    )


def _put_session(
    store: InMemoryMultiTenantStore,
    user: UserRecord,
    *,
    token: str = "browser-token",
) -> None:
    store.put_user(user)
    store.put_session(
        token,
        SessionRecord(
            user_id=user.user_id,
            csrf_token="csrf-token",
            expires_at=int(time.time()) + 600,
            connection_version=user.connection_version,
        ),
    )


def test_session_from_previous_connection_is_rejected_after_reconnect() -> None:
    settings = _settings()
    store = InMemoryMultiTenantStore()
    now = int(time.time())
    _put_session(
        store,
        UserRecord(
            user_id="u1",
            email="user@example.com",
            encrypted_grant="cipher-v1",
            consent_version=settings.disclosure_version,
            consented_at=now,
            connection_version="v1",
        ),
    )
    store.upsert_user_connection(
        "u1",
        "user@example.com",
        "cipher-v2",
        now + 1,
        settings.disclosure_version,
        now + 1,
        "v2",
        "v1",
    )
    client = TestClient(create_app(settings=settings, runtime=_runtime(settings, store)))
    client.cookies.set(settings.cookie_name, "browser-token")

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert store.get_session("browser-token") is None


def test_save_policy_rejects_a_stale_connection_generation() -> None:
    store = InMemoryMultiTenantStore()
    original = _policy()
    store.put_user(
        UserRecord(
            user_id="u1",
            email="user@example.com",
            encrypted_grant="cipher-v2",
            policy=original,
            connection_version="v2",
        )
    )
    changed = ClassificationPolicy(
        prompt="Label newsletters and product announcements as News.",
        labels=["News"],
    )

    with pytest.raises(RuntimeError, match="connection changed"):
        store.save_policy("u1", "v1", changed)

    assert store.get_user("u1").policy == original


def test_stale_consent_dashboard_offers_reacceptance_and_gates_operations() -> None:
    settings = _settings()
    store = InMemoryMultiTenantStore()
    policy = _policy()
    _put_session(
        store,
        UserRecord(
            user_id="u1",
            email="user@example.com",
            encrypted_grant="cipher",
            policy=policy,
            consent_version="old-disclosure",
            connection_version="v1",
        ),
    )
    client = TestClient(create_app(settings=settings, runtime=_runtime(settings, store)))
    client.cookies.set(settings.cookie_name, "browser-token")

    dashboard = client.get("/dashboard")
    settings_write = client.post(
        "/settings",
        data={
            "csrf_token": "csrf-token",
            "prompt": "Label newsletters and product announcements as News.",
            "labels": "News",
            "gmail_query": "in:inbox",
            "confidence_threshold": "0.85",
            "max_messages": "10",
        },
        follow_redirects=False,
    )
    preview = client.post(
        "/runs/preview",
        data={"csrf_token": "csrf-token"},
        follow_redirects=False,
    )

    assert dashboard.status_code == 200
    assert 'action="/consent"' in dashboard.text
    assert "Review the updated privacy notice" in dashboard.text
    assert settings_write.status_code == 303
    assert "Accept+the+current+privacy+notice" in settings_write.headers["location"]
    assert preview.status_code == 303
    assert "could+not+be+queued" in preview.headers["location"]
    assert store.get_user("u1").policy == policy
    assert store.list_runs("u1") == []


def test_accept_current_notice_restores_consent_and_automatic_schedule() -> None:
    settings = _settings()
    store = InMemoryMultiTenantStore()
    policy = _policy(automatic=True)
    user = UserRecord(
        user_id="u1",
        email="user@example.com",
        encrypted_grant="cipher",
        policy=policy,
        next_run_at=123,
        consent_version="old-disclosure",
        connection_version="v1",
        schedule_paused_for_consent=True,
    )
    store.put_user(user)

    updated = _runtime(settings, store).accept_current_notice(user)

    assert updated.consent_version == settings.disclosure_version
    assert updated.consented_at > 0
    assert updated.schedule_paused_for_consent is False
    assert updated.next_run_at == 0
    assert store.get_user("u1") == updated


def test_stale_dispatcher_pause_cannot_overwrite_newly_accepted_consent() -> None:
    settings = _settings()
    store = InMemoryMultiTenantStore()
    policy = _policy(automatic=True)
    user = UserRecord(
        user_id="u1",
        email="user@example.com",
        encrypted_grant="cipher",
        policy=policy,
        next_run_at=0,
        consent_version="old-disclosure",
        connection_version="v1",
    )
    store.put_user(user)
    stale_projection = store.list_automatic_users(due_before=0)[0]
    accepted = _runtime(settings, store).accept_current_notice(user)

    paused = store.pause_automatic_user(
        stale_projection.user_id,
        stale_projection.next_run_at,
        stale_projection.connection_version,
        stale_projection.consent_version,
    )

    assert paused is False
    current = store.get_user("u1")
    assert current == accepted
    assert current.consent_version == settings.disclosure_version
    assert current.schedule_paused_for_consent is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("prompt", "   \t\n"), ("gmail_query", "  \t ")],
)
def test_policy_rejects_whitespace_only_required_text(field: str, value: str) -> None:
    values = {
        "prompt": "Label invoices and payment receipts as Finance.",
        "labels": ["Finance"],
        "gmail_query": "in:inbox",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        ClassificationPolicy(**values)


def test_activation_hash_changes_when_only_max_messages_changes() -> None:
    smaller = _policy(max_messages=5)
    larger = _policy(max_messages=6)

    assert smaller.policy_hash == larger.policy_hash
    assert smaller.activation_hash != larger.activation_hash
