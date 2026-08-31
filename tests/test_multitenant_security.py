import json
import time

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from email_classification_agent.multitenant_store import (
    InMemoryMultiTenantStore,
    _expected_transaction_condition,
    _normalized_message_rows,
    _normalized_property_rows,
    _normalized_run_row,
    _oauth_state_to_dict,
    _policy_revision_to_dict,
    _run_to_dict,
    _session_to_dict,
    _user_to_dict,
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
    PolicyRevision,
    RunRecord,
    SessionRecord,
    UniversalOutcome,
    UniversalReport,
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


def test_postgres_snapshot_serialization_adds_human_readable_timestamps() -> None:
    policy = ClassificationPolicy(
        prompt="Label invoices and payment receipts as Finance.",
        labels=["Finance"],
    )
    user = UserRecord(
        user_id="u1",
        email="u@example.com",
        encrypted_grant="cipher",
        policy=policy,
        created_at=1_700_000_000,
        updated_at=1_700_000_100,
        next_run_at=0,
        consented_at=1_700_000_050,
        connection_version="v1",
    )
    session = SessionRecord(
        user_id="u1",
        csrf_token="csrf",
        expires_at=1_700_000_200,
        connection_version="v1",
    )
    oauth_state = OAuthStateRecord(
        code_verifier="verifier",
        expires_at=1_700_000_300,
        consented_at=1_700_000_250,
    )

    user_data = _user_to_dict(user)
    session_data = _session_to_dict(session)
    oauth_data = _oauth_state_to_dict(oauth_state)

    assert user_data["created_at"] == 1_700_000_000
    assert user_data["created_at_readable"] == "2023-11-14 22:13:20 UTC"
    assert user_data["updated_at_readable"] == "2023-11-14 22:15:00 UTC"
    assert user_data["next_run_at_readable"] == "not set"
    assert user_data["consented_at_readable"] == "2023-11-14 22:14:10 UTC"
    assert user_data["policy"] == policy.model_dump(mode="json")
    assert session_data["expires_at_readable"] == "2023-11-14 22:16:40 UTC"
    assert oauth_data["expires_at_readable"] == "2023-11-14 22:18:20 UTC"


def test_run_and_policy_revision_snapshots_include_readable_json_and_times() -> None:
    report = UniversalReport(
        mailbox="u@example.com",
        dry_run=True,
        policy_hash="hash",
        processed_label="EmailAgent/Processed/hash",
        outcomes=[
            UniversalOutcome(
                message_id="m1",
                thread_id="t1",
                subject="Invoice",
                sender="Vendor <vendor@example.com>",
                proposed_label="Finance",
                confidence=0.99,
                action="would_add:Finance",
                reason="Invoice",
            )
        ],
    )
    json_report = json.dumps(report.as_dict())
    run = RunRecord(
        run_id="run1",
        user_id="u1",
        mode="preview",
        status="completed",
        policy_hash="hash",
        created_at=1_700_000_000,
        updated_at=1_700_000_100,
        expires_at=1_700_086_400,
        report_json=json_report,
        lease_expires_at=0,
        connection_version="v1",
    )
    revision = PolicyRevision(
        policy_hash="hash",
        saved_at=1_700_000_050,
        policy_json=ClassificationPolicy(
            prompt="Label invoices and payment receipts as Finance.",
            labels=["Finance"],
        ).model_dump_json(),
        connection_version="v1",
    )

    run_data = _run_to_dict(run)
    revision_data = _policy_revision_to_dict(revision)

    assert run_data["report_json"] == json_report
    assert run_data["report"]["outcomes"][0]["subject"] == "Invoice"
    assert run_data["created_at_readable"] == "2023-11-14 22:13:20 UTC"
    assert run_data["expires_at_readable"] == "2023-11-15 22:13:20 UTC"
    assert run_data["lease_expires_at_readable"] == "not set"
    assert revision_data["policy"]["labels"] == ["Finance"]
    assert revision_data["saved_at_readable"] == "2023-11-14 22:14:10 UTC"


def test_normalized_run_rows_are_pgadmin_readable() -> None:
    run = RunRecord(
        run_id="run-normalized",
        user_id="u1",
        mode="automatic",
        status="completed",
        policy_hash="hash",
        created_at=1_700_000_000,
        updated_at=1_700_000_100,
        expires_at=1_700_086_400,
        connection_version="v1",
        report_json=json.dumps(
            {
                "mailbox": "buyer@example.com",
                "scanned": 1,
                "proposed": 1,
                "labeled": 1,
                "outcomes": [
                    {
                        "message_id": "m1",
                        "thread_id": "t1",
                        "subject": "Dade County target",
                        "sender": "Deals <deals@example.com>",
                        "proposed_label": "Acquisitions/Wholesale",
                        "secondary_label": (
                            "Acquisitions/Wholesale/Miami-Dade/Important"
                        ),
                        "confidence": 0.99,
                        "action": "label_and_move",
                        "reason": "Matched target property criteria.",
                        "evidence": ["Folio 30"],
                        "property_records": [
                            {
                                "address": "16225 NE 2nd Ave",
                                "asking_price": 262500,
                                "folio": "30-2218-007-2720",
                                "is_folio_30": True,
                                "is_unincorporated": True,
                                "has_double_lot": True,
                                "qualifies": True,
                                "reasons": ["price within target"],
                            }
                        ],
                    }
                ],
            }
        ),
    )
    user = UserRecord(
        user_id="u1",
        email="buyer@example.com",
        encrypted_grant="cipher",
        connection_version="v1",
    )

    run_row = _normalized_run_row(run, user)
    message_rows = _normalized_message_rows(run)
    property_rows = _normalized_property_rows(message_rows[0])

    assert run_row["mailbox"] == "buyer@example.com"
    assert run_row["run_date_miami"] == "2023-11-14"
    assert run_row["run_time_miami"] == "2023-11-14 05:13 PM EST"
    assert run_row["scanned"] == 1
    assert message_rows[0]["subject"] == "Dade County target"
    assert message_rows[0]["is_important"] is True
    assert "16225 NE 2nd Ave" in message_rows[0]["why_important"]
    assert property_rows[0]["address"] == "16225 NE 2nd Ave"
    assert property_rows[0]["qualifies"] is True


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
