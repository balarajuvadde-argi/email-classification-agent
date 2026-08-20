from urllib.parse import parse_qs, urlsplit

import pytest

from email_classification_agent.google_oauth import GoogleOAuthManager

REDIRECT = "http://localhost:8000/oauth/google/callback"
CLIENT_CONFIG = {
    "web": {
        "client_id": "client.apps.googleusercontent.com",
        "client_secret": "secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT],
    }
}


def test_connect_flow_does_not_inherit_previously_granted_scopes() -> None:
    manager = GoogleOAuthManager(CLIENT_CONFIG, REDIRECT)

    url, verifier, nonce = manager.authorization_url("state", gmail_access=True)
    query = parse_qs(urlsplit(url).query)

    assert query["include_granted_scopes"] == ["false"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent select_account"]
    assert "https://www.googleapis.com/auth/gmail.modify" in query["scope"][0]
    assert verifier
    assert nonce


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("auth_uri", "https://attacker.example/auth", "authorization endpoint"),
        ("token_uri", "https://attacker.example/token", "token endpoint"),
        ("client_id", "not-google.example", "unexpected issuer"),
    ],
)
def test_google_oauth_rejects_untrusted_client_endpoints(
    field: str,
    value: str,
    message: str,
) -> None:
    web = {**CLIENT_CONFIG["web"], field: value}

    with pytest.raises(RuntimeError, match=message):
        GoogleOAuthManager({"web": web}, REDIRECT)


def test_google_oauth_requires_exact_registered_redirect() -> None:
    with pytest.raises(RuntimeError, match="not registered"):
        GoogleOAuthManager(CLIENT_CONFIG, "http://localhost:9000/oauth/google/callback")
