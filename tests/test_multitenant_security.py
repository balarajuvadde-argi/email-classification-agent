import time

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from email_classification_agent.multitenant_store import (
    InMemoryMultiTenantStore,
    _expected_transaction_condition,
)
from email_classification_agent.token_security import (
    FernetTokenCipher,
    KmsTokenCipher,
    credentials_from_grant,
    credentials_to_grant,
)
from email_classification_agent.universal_models import (
    ClassificationPolicy,
    OAuthStateRecord,
    SessionRecord,
    UserRecord,
)


def test_oauth_state_is_single_use_and_expires() -> None:
    store = InMemoryMultiTenantStore()
    store.put_oauth_state(
        "state",
        OAuthStateRecord(code_verifier="verifier", expires_at=int(time.time()) + 60),
    )

    assert store.consume_oauth_state("state") is not None
    assert store.consume_oauth_state("state") is None

    store.put_oauth_state(
        "old",
        OAuthStateRecord(code_verifier="verifier", expires_at=int(time.time()) - 1),
    )
    assert store.consume_oauth_state("old") is None


def test_sessions_are_hashed_and_tenant_user_must_exist() -> None:
    store = InMemoryMultiTenantStore()
    store.put_user(
        UserRecord(
            user_id="u1",
            email="u@example.com",
            encrypted_grant="cipher",
            connection_version="v1",
        )
    )
    store.put_session(
        "raw-browser-secret",
        SessionRecord(
            user_id="u1",
            csrf_token="csrf",
            expires_at=int(time.time()) + 60,
            connection_version="v1",
        ),
    )

    assert "raw-browser-secret" not in store.sessions
    assert store.get_session("wrong") is None
    assert store.get_session("raw-browser-secret").user_id == "u1"


class _Kms:
    def __init__(self):
        self.contexts = []

    def encrypt(self, **kwargs):
        self.contexts.append(kwargs["EncryptionContext"])
        return {"CiphertextBlob": b"cipher" + bytes(kwargs["Plaintext"])}

    def decrypt(self, **kwargs):
        self.contexts.append(kwargs["EncryptionContext"])
        return {"Plaintext": bytes(kwargs["CiphertextBlob"])[6:]}


def test_kms_cipher_binds_ciphertext_operations_to_user_context() -> None:
    kms = _Kms()
    cipher = KmsTokenCipher("key", client=kms)

    encrypted = cipher.encrypt("user-123", b"refresh-token")
    assert cipher.decrypt("user-123", encrypted) == b"refresh-token"
    assert kms.contexts[0] == kms.contexts[1]
    assert kms.contexts[0]["application"] == "universal-email-classifier"
    assert kms.contexts[0]["user_ref"] != "user-123"
    assert len(kms.contexts[0]["user_ref"]) == 64


def test_dynamo_transaction_conflicts_are_not_mistaken_for_business_conditions() -> None:
    conditional = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [
                {"Code": "ConditionalCheckFailed"},
                {"Code": "None"},
            ],
        },
        "TransactWriteItems",
    )
    conflict = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [{"Code": "TransactionConflict"}],
        },
        "TransactWriteItems",
    )

    assert _expected_transaction_condition(conditional) is True
    assert _expected_transaction_condition(conflict) is False


def test_stored_user_grant_is_encrypted_and_excludes_shared_client_secret() -> None:
    credentials = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="shared-client",
        client_secret="shared-secret",
        scopes=["openid", "email", "https://www.googleapis.com/auth/gmail.modify"],
    )
    grant = credentials_to_grant(credentials)

    assert b"refresh" in grant
    assert b"access" not in grant
    assert b"shared-secret" not in grant
    assert b"openid" not in grant
    assert b"gmail.modify" in grant
    cipher = FernetTokenCipher(Fernet.generate_key())
    encrypted = cipher.encrypt("u1", grant)
    assert "refresh" not in encrypted

    restored = credentials_from_grant(
        cipher.decrypt("u1", encrypted),
        {
            "web": {
                "client_id": "shared-client",
                "client_secret": "shared-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
    )
    assert restored.refresh_token == "refresh"
    assert restored.token is None
    assert restored.client_secret == "shared-secret"


def test_automatic_users_are_due_and_claimed_atomically_in_memory() -> None:
    store = InMemoryMultiTenantStore()
    policy = ClassificationPolicy(
        prompt="Label invoices and payment receipts as Finance.",
        labels=["Finance"],
        automatic_enabled=True,
    )
    store.put_user(
        UserRecord(
            user_id="u1",
            email="u@example.com",
            encrypted_grant="cipher",
            policy=policy,
            next_run_at=100,
            connection_version="v1",
        )
    )

    assert store.list_automatic_users(due_before=99) == []
    assert [user.user_id for user in store.list_automatic_users(due_before=100)] == ["u1"]
    assert store.claim_automatic_user("u1", 100, 400, "v1") is True
    assert store.claim_automatic_user("u1", 100, 500, "v1") is False
    assert store.list_automatic_users(due_before=399) == []


def test_delete_user_removes_user_runs_plans_and_sessions_in_memory() -> None:
    store = InMemoryMultiTenantStore()
    user = UserRecord(
        user_id="u1",
        email="u@example.com",
        encrypted_grant="cipher",
        connection_version="v1",
    )
    store.put_user(user)
    store.put_session(
        "token",
        SessionRecord(
            user_id="u1",
            csrf_token="csrf",
            expires_at=int(time.time()) + 60,
            connection_version="v1",
        ),
    )

    store.delete_user("u1", "v1")

    assert store.get_user("u1") is None
    assert store.get_session("token") is None
