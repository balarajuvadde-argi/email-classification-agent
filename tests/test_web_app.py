import json
import time

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from email_classification_agent.multitenant_store import InMemoryMultiTenantStore
from email_classification_agent.token_security import FernetTokenCipher
from email_classification_agent.universal_models import (
    ClassificationPolicy,
    RunRecord,
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


class _OAuth:
    client_config = CLIENT_CONFIG

    def authorization_url(self, state, *, gmail_access):
        self.gmail_access = gmail_access
        return (
            f"https://accounts.google.com/o/oauth2/auth?state={state}",
            "verifier",
            "id-token-nonce",
        )


def _runtime():
    settings = _settings()
    store = InMemoryMultiTenantStore()
    runtime = WebRuntime(
        settings,
        store=store,
        cipher=FernetTokenCipher(settings.local_token_encryption_key),
        oauth=_OAuth(),
        classifier_factory=lambda: None,
    )
    return settings, store, runtime


def _authenticated_client(policy=None):
    settings, store, runtime = _runtime()
    user = UserRecord(
        user_id="u1",
        email="user@example.com",
        encrypted_grant="cipher",
        policy=policy,
        created_at=int(time.time()),
        updated_at=int(time.time()),
        connection_version="v1",
        consent_version=settings.disclosure_version,
    )
    store.put_user(user)
    store.put_session(
        "browser-token",
        SessionRecord(
            user_id="u1",
            csrf_token="csrf-token",
            expires_at=int(time.time()) + 600,
            connection_version="v1",
        ),
    )
    client = TestClient(create_app(settings=settings, runtime=runtime))
    client.cookies.set(settings.cookie_name, "browser-token")
    return client, store


def test_landing_has_privacy_consent_and_security_headers() -> None:
    settings, _, runtime = _runtime()
    client = TestClient(create_app(settings=settings, runtime=runtime))

    response = client.get("/")

    assert response.status_code == 200
    assert "Connect Gmail securely" in response.text
    assert "sent securely to OpenAI" in response.text
    assert "Returning user? Sign in" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_local_loopback_host_is_redirected_to_configured_oauth_host() -> None:
    settings, _, runtime = _runtime()
    client = TestClient(
        create_app(settings=settings, runtime=runtime),
        base_url="http://127.0.0.1:8000",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:8000/"


def test_connect_requires_consent_and_creates_server_side_oauth_state() -> None:
    settings, store, runtime = _runtime()
    client = TestClient(create_app(settings=settings, runtime=runtime))

    denied = client.post("/connect/google", data={}, follow_redirects=False)
    allowed = client.post(
        "/connect/google",
        data={"consent": "yes"},
        follow_redirects=False,
    )

    assert denied.status_code == 422
    assert allowed.status_code == 303
    assert allowed.headers["location"].startswith("https://accounts.google.com/")
    assert "email_agent_session_oauth=" in allowed.headers["set-cookie"]
    assert "HttpOnly" in allowed.headers["set-cookie"]
    assert len(store.states) == 1
    assert all(key != "verifier" for key in store.states)
    state_record = next(iter(store.states.values()))
    assert state_record.browser_nonce_hash
    assert state_record.id_token_nonce == "id-token-nonce"
    assert state_record.purpose == "connect"
    assert runtime.oauth.gmail_access is True


def test_returning_sign_in_requests_identity_scopes_only() -> None:
    settings, store, runtime = _runtime()
    client = TestClient(create_app(settings=settings, runtime=runtime))

    response = client.post("/signin/google", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert runtime.oauth.gmail_access is False
    state_record = next(iter(store.states.values()))
    assert state_record.purpose == "signin"


def test_dashboard_escapes_user_prompt_and_model_control_text() -> None:
    policy = ClassificationPolicy(
        prompt="Label invoices as Finance. <script>alert('x')</script>",
        labels=["Finance"],
    )
    client, _ = _authenticated_client(policy)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "Suggested workflows" in response.text
    assert "All acquisitions + news" in response.text
    assert "On-Market only" in response.text
    assert "+ Add new classification" in response.text


def test_settings_write_requires_csrf_and_rejects_system_label() -> None:
    client, store = _authenticated_client()
    base = {
        "prompt": "Label invoices and receipts as Finance.",
        "gmail_query": "in:inbox",
        "confidence_threshold": "0.85",
        "max_messages": "10",
    }

    bad_csrf = client.post("/settings", data={**base, "labels": "Finance", "csrf_token": "bad"})
    bad_label = client.post(
        "/settings",
        data={**base, "labels": "INBOX", "csrf_token": "csrf-token"},
        follow_redirects=False,
    )
    good = client.post(
        "/settings",
        data={**base, "labels": "Finance\nNews", "csrf_token": "csrf-token"},
        follow_redirects=False,
    )

    assert bad_csrf.status_code == 403
    assert "error=" in bad_label.headers["location"]
    assert good.status_code == 303
    assert store.get_user("u1").policy.labels == ["Finance", "News"]
    revisions = store.list_policy_revisions("u1", "v1")
    assert len(revisions) == 1
    assert revisions[0].policy.prompt == base["prompt"]
    assert revisions[0].policy_hash == store.get_user("u1").policy.policy_hash


def test_template_policy_can_be_saved_from_dashboard() -> None:
    client, store = _authenticated_client()

    response = client.post(
        "/settings/template",
        data={"csrf_token": "csrf-token", "template_id": "on_market_only"},
        follow_redirects=False,
    )

    policy = store.get_user("u1").policy
    assert response.status_code == 303
    assert policy is not None
    assert policy.labels == ["Acquisitions/On Market"]
    assert "Zillow, Redfin, MLS/Matrix, OneHome" in policy.prompt
    assert policy.automatic_enabled is False
    assert len(store.list_policy_revisions("u1", "v1")) == 1


def test_custom_classification_appends_to_saved_policy() -> None:
    policy = ClassificationPolicy(
        prompt="Label newsletters and market updates as News.",
        labels=["News"],
        automatic_enabled=True,
    )
    client, store = _authenticated_client(policy)

    response = client.post(
        "/settings/classifications",
        data={
            "csrf_token": "csrf-token",
            "custom_label": "Leads/Investors",
            "custom_rule": (
                "the sender asks about buying, selling, funding, or partnership "
                "opportunities"
            ),
        },
        follow_redirects=False,
    )

    saved = store.get_user("u1").policy
    assert response.status_code == 303
    assert saved is not None
    assert saved.labels == ["News", "Leads/Investors"]
    assert "Additional user classification" in saved.prompt
    assert "Leads/Investors" in saved.prompt
    assert saved.automatic_enabled is False


def test_user_cannot_read_another_users_run() -> None:
    client, store = _authenticated_client()
    now = int(time.time())
    store.put_user(
        UserRecord(
            user_id="u2",
            email="other@example.com",
            encrypted_grant="cipher",
            connection_version="v2",
        )
    )
    store.put_run(
        RunRecord(
            run_id=f"{now}-other",
            user_id="u2",
            mode="preview",
            status="completed",
            policy_hash="hash",
            created_at=now,
            updated_at=now,
            expires_at=now + 60,
            connection_version="v2",
        )
    )

    response = client.get(f"/runs/{now}-other")

    assert response.status_code == 404
    assert "No Gmail action was taken" in response.text
