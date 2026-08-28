from datetime import UTC, datetime
from types import SimpleNamespace

from email_classification_agent import scheduled_runner
from email_classification_agent.schedule import (
    is_in_scheduled_window,
    next_scheduled_timestamp,
    parse_local_times,
)
from email_classification_agent.universal_models import ClassificationPolicy, UserRecord
from email_classification_agent.web_config import WebSettings

CLIENT_CONFIG_JSON = (
    '{"web":{"client_id":"client.apps.googleusercontent.com","client_secret":"secret",'
    '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
    '"token_uri":"https://oauth2.googleapis.com/token",'
    '"redirect_uris":["http://localhost:8000/oauth/google/callback"]}}'
)


def _timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


def _settings() -> WebSettings:
    return WebSettings(
        environment="development",
        app_base_url="http://localhost:8000",
        table_name="",
        kms_key_id=None,
        local_token_encryption_key="test-key",
        google_oauth_secret_id=None,
        google_oauth_client_config_json=CLIENT_CONFIG_JSON,
        google_oauth_client_file=None,
        openai_api_key_secret_id=None,
        openai_api_key="test",
        openai_model="test",
        queue_url=None,
        aws_region=None,
        automatic_schedule_timezone="America/New_York",
        automatic_schedule_local_times=("10:00", "17:00"),
        automatic_schedule_window_seconds=900,
    )


def test_miami_schedule_handles_dst_and_standard_time() -> None:
    local_times = parse_local_times("10:00,17:00")

    assert next_scheduled_timestamp(
        _timestamp(2026, 8, 28, 13),
        timezone_name="America/New_York",
        local_times=local_times,
    ) == _timestamp(2026, 8, 28, 14)
    assert next_scheduled_timestamp(
        _timestamp(2026, 1, 15, 14),
        timezone_name="America/New_York",
        local_times=local_times,
    ) == _timestamp(2026, 1, 15, 15)


def test_miami_schedule_window_allows_only_the_configured_local_times() -> None:
    local_times = parse_local_times("10:00,17:00")

    assert is_in_scheduled_window(
        _timestamp(2026, 8, 28, 14, 5),
        timezone_name="America/New_York",
        local_times=local_times,
        window_seconds=900,
    )
    assert not is_in_scheduled_window(
        _timestamp(2026, 8, 28, 15),
        timezone_name="America/New_York",
        local_times=local_times,
        window_seconds=900,
    )


def test_scheduled_runner_postpones_due_users_until_next_miami_window(monkeypatch) -> None:
    settings = _settings()
    policy = ClassificationPolicy(
        prompt="Label news newsletter emails as News.",
        labels=["News"],
        automatic_enabled=True,
    )
    user = UserRecord(
        user_id="u1",
        email="u@example.com",
        encrypted_grant="cipher",
        policy=policy,
        consent_version=settings.disclosure_version,
        next_run_at=0,
        connection_version="v1",
    )

    class _Store:
        claims = []

        def list_automatic_users(self, *, limit, due_before):
            del limit, due_before
            return [user]

        def claim_automatic_user(
            self,
            user_id,
            expected_next_run_at,
            new_next_run_at,
            connection_version,
        ):
            self.claims.append(
                (user_id, expected_next_run_at, new_next_run_at, connection_version)
            )
            return True

    store = _Store()

    class _Runtime:
        def __init__(self, runtime_settings):
            del runtime_settings
            self.store = store

        def new_run(self, user, *, mode):
            raise AssertionError("off-window scheduler must not queue a run")

    monkeypatch.setattr(scheduled_runner.WebSettings, "from_env", lambda: settings)
    monkeypatch.setattr(scheduled_runner, "WebRuntime", _Runtime)
    monkeypatch.setattr(scheduled_runner.time, "time", lambda: _timestamp(2026, 8, 28, 13))

    assert scheduled_runner.run_due_users() == 0
    assert store.claims == [("u1", 0, _timestamp(2026, 8, 28, 14), "v1")]


def test_scheduled_runner_queues_due_users_inside_miami_window(monkeypatch) -> None:
    settings = _settings()
    policy = ClassificationPolicy(
        prompt="Label news newsletter emails as News.",
        labels=["News"],
        automatic_enabled=True,
    )
    user = UserRecord(
        user_id="u1",
        email="u@example.com",
        encrypted_grant="cipher",
        policy=policy,
        consent_version=settings.disclosure_version,
        next_run_at=0,
        connection_version="v1",
    )

    class _Store:
        claims = []

        def list_automatic_users(self, *, limit, due_before):
            del limit, due_before
            return [user]

        def claim_automatic_user(
            self,
            user_id,
            expected_next_run_at,
            new_next_run_at,
            connection_version,
        ):
            self.claims.append(
                (user_id, expected_next_run_at, new_next_run_at, connection_version)
            )
            return True

        def get_user(self, user_id):
            del user_id
            return user

        def consume_quota(self, *args):
            del args
            return True

    store = _Store()
    runs = []

    class _Runtime:
        def __init__(self, runtime_settings):
            del runtime_settings
            self.store = store

        def new_run(self, user, *, mode):
            runs.append((user.user_id, mode))
            return SimpleNamespace(run_id="run1")

    monkeypatch.setattr(scheduled_runner.WebSettings, "from_env", lambda: settings)
    monkeypatch.setattr(scheduled_runner, "WebRuntime", _Runtime)
    monkeypatch.setattr(scheduled_runner.time, "time", lambda: _timestamp(2026, 8, 28, 14, 5))

    assert scheduled_runner.run_due_users() == 1
    assert store.claims == [("u1", 0, _timestamp(2026, 8, 28, 21), "v1")]
    assert runs == [("u1", "automatic")]
