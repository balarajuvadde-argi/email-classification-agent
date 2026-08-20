import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from email_classification_agent.multitenant_store import InMemoryMultiTenantStore
from email_classification_agent.token_security import FernetTokenCipher
from email_classification_agent.web_app import create_app
from email_classification_agent.web_config import WebSettings, load_google_client_config
from email_classification_agent.web_runtime import WebRuntime
CLIENT_CONFIG = {
    "web": {
        "client_id": "client.apps.googleusercontent.com",
        "client_secret": "secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["https://localhost:8443/oauth/google/callback"],
    }
}


class _OAuth:
    client_config = CLIENT_CONFIG

    def authorization_url(self, state, *, gmail_access):
        del gmail_access
        return (
            f"https://accounts.google.com/o/oauth2/auth?state={state}",
            "verifier",
            "id-token-nonce",
        )


def _https_local_settings() -> WebSettings:
    return WebSettings(
        environment="development",
        app_base_url="https://localhost:8443",
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


def _production_settings() -> WebSettings:
    return WebSettings(
        environment="production",
        app_base_url="https://inbox.example.org",
        table_name="table",
        kms_key_id="key",
        local_token_encryption_key=None,
        google_oauth_secret_id="google-secret",
        google_oauth_client_config_json=None,
        google_oauth_client_file=None,
        openai_api_key_secret_id="openai-secret",
        openai_api_key=None,
        openai_model="gpt-5-mini",
        queue_url="https://sqs.example.invalid/queue.fifo",
        aws_region="us-east-1",
        operator_name="Example Operations LLC",
        privacy_contact_email="privacy@example.org",
        support_email="support@example.org",
        privacy_notice_version="2026-08-20.1",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("environment", "prod", "APP_ENV"),
        ("app_base_url", "http://localhost.evil.example", "HTTPS"),
        ("app_base_url", "https://inbox.example.org/base", "path"),
        ("ai_provider_name", "Another Provider", "OpenAI"),
        ("backup_recovery_days", 36, "BACKUP_RECOVERY_DAYS"),
    ],
)
def test_web_settings_fail_closed(field: str, value: object, message: str) -> None:
    settings = replace(_production_settings(), **{field: value})

    with pytest.raises(ValueError, match=message):
        settings.validate()


def test_public_url_cannot_use_development_mode() -> None:
    settings = replace(
        _https_local_settings(),
        environment="development",
        app_base_url="https://inbox.example.org",
    )

    with pytest.raises(ValueError, match="public APP_BASE_URL"):
        settings.validate()


def test_automatic_quota_must_cover_the_configured_cadence() -> None:
    settings = replace(
        _production_settings(),
        automatic_interval_seconds=300,
        automatic_runs_per_day=96,
    )

    with pytest.raises(ValueError, match="cover the configured automatic interval"):
        settings.validate()


def test_notice_revision_changes_the_consent_version() -> None:
    first = _production_settings()
    second = replace(first, privacy_notice_version="2026-08-20.2")

    assert first.disclosure_version != second.disclosure_version


def test_google_client_config_json_accepts_a_local_file_path(tmp_path) -> None:
    config_path = tmp_path / "client-secret.json"
    config_path.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    settings = replace(
        _https_local_settings(),
        google_oauth_client_config_json=str(config_path),
    )
    load_google_client_config.cache_clear()

    assert load_google_client_config(settings) == CLIENT_CONFIG


def test_https_app_sets_hsts_and_secure_oauth_cookie() -> None:
    settings = _https_local_settings()
    settings.validate()
    local_key = Fernet.generate_key()
    runtime = WebRuntime(
        settings,
        store=InMemoryMultiTenantStore(),
        cipher=FernetTokenCipher(local_key),
        oauth=_OAuth(),
        classifier_factory=lambda: None,
    )
    runtime.client_config = CLIENT_CONFIG
    client = TestClient(create_app(settings=settings, runtime=runtime), base_url=settings.app_base_url)

    landing = client.get("/")
    connect = client.post(
        "/connect/google",
        data={"consent": "yes"},
        follow_redirects=False,
    )

    assert landing.headers["strict-transport-security"].startswith("max-age=")
    cookie = connect.headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
