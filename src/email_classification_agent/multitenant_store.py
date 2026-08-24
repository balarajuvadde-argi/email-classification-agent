from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import replace
from threading import RLock
from typing import Any, Protocol

from .universal_models import (
    ActionPlan,
    ClassificationPolicy,
    OAuthStateRecord,
    PolicyRevision,
    RunRecord,
    ScheduledUserRecord,
    SessionRecord,
    UniversalOutcome,
    UserRecord,
)

LOGGER = logging.getLogger(__name__)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected_transaction_condition(exc: Any) -> bool:
    """Return true only for an expected DynamoDB conditional cancellation."""
    response = getattr(exc, "response", {})
    code = response.get("Error", {}).get("Code")
    if code == "ConditionalCheckFailedException":
        return True
    if code != "TransactionCanceledException":
        return False
    reasons = response.get("CancellationReasons") or []
    reason_codes = [reason.get("Code") for reason in reasons if reason.get("Code")]
    return bool(reason_codes) and "ConditionalCheckFailed" in reason_codes and all(
        reason_code in {"None", "ConditionalCheckFailed"}
        for reason_code in reason_codes
    )


class MultiTenantStore(Protocol):
    def put_oauth_state(self, state: str, record: OAuthStateRecord) -> None: ...

    def consume_oauth_state(self, state: str) -> OAuthStateRecord | None: ...

    def put_session(self, token: str, record: SessionRecord) -> None: ...

    def get_session(self, token: str) -> SessionRecord | None: ...

    def delete_session(self, token: str) -> None: ...

    def put_user(self, user: UserRecord) -> None: ...

    def upsert_user_connection(
        self,
        user_id: str,
        email: str,
        encrypted_grant: str,
        now: int,
        consent_version: str,
        consented_at: int,
        connection_version: str,
        expected_connection_version: str | None,
    ) -> UserRecord: ...

    def get_user(self, user_id: str) -> UserRecord | None: ...

    def save_policy(
        self,
        user_id: str,
        connection_version: str,
        policy: ClassificationPolicy,
    ) -> UserRecord: ...

    def list_policy_revisions(
        self, user_id: str, connection_version: str, limit: int = 20
    ) -> list[PolicyRevision]: ...

    def mark_policy_previewed(
        self,
        user_id: str,
        activation_hash: str,
        connection_version: str,
    ) -> bool: ...

    def update_consent(
        self,
        user_id: str,
        connection_version: str,
        consent_version: str,
        consented_at: int,
        automatic_enabled: bool,
        policy_json: str,
    ) -> UserRecord: ...

    def list_automatic_users(
        self,
        limit: int = 100,
        due_before: int | None = None,
    ) -> list[ScheduledUserRecord]: ...

    def claim_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        new_next_run_at: int,
        connection_version: str,
    ) -> bool: ...

    def pause_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        connection_version: str,
        consent_version: str,
    ) -> bool: ...

    def delete_user(self, user_id: str, connection_version: str) -> None: ...

    def acquire_active_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        expires_at: int,
        connection_version: str,
    ) -> bool: ...

    def release_active_run(self, user_id: str, run_id: str) -> None: ...

    def consume_quota(
        self,
        user_id: str,
        connection_version: str,
        quota_name: str,
        limit: int,
        window_seconds: int,
        now: int,
    ) -> bool: ...

    def put_plan(self, plan_id: str, plan: ActionPlan) -> None: ...

    def get_plan(self, plan_id: str) -> ActionPlan | None: ...

    def consume_plan(self, plan_id: str) -> ActionPlan | None: ...

    def put_run(self, run: RunRecord) -> None: ...

    def update_run(self, run: RunRecord) -> bool: ...

    def claim_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        lease_expires_at: int,
    ) -> RunRecord | None: ...

    def get_run(self, user_id: str, run_id: str) -> RunRecord | None: ...

    def list_runs(self, user_id: str, limit: int = 10) -> list[RunRecord]: ...


class InMemoryMultiTenantStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.states: dict[str, OAuthStateRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.users: dict[str, UserRecord] = {}
        self.plans: dict[str, ActionPlan] = {}
        self.runs: dict[tuple[str, str], RunRecord] = {}
        self.policy_revisions: dict[tuple[str, int, str], PolicyRevision] = {}
        self.active_runs: dict[str, tuple[str, int, str]] = {}
        self.quotas: dict[tuple[str, str, int], int] = {}

    @staticmethod
    def _alive(expires_at: int) -> bool:
        return expires_at > int(time.time())

    def put_oauth_state(self, state: str, record: OAuthStateRecord) -> None:
        with self._lock:
            self.states[secret_hash(state)] = record

    def consume_oauth_state(self, state: str) -> OAuthStateRecord | None:
        with self._lock:
            record = self.states.pop(secret_hash(state), None)
        return record if record and self._alive(record.expires_at) else None

    def put_session(self, token: str, record: SessionRecord) -> None:
        with self._lock:
            user = self.users.get(record.user_id)
            if user is None or user.connection_version != record.connection_version:
                raise RuntimeError("Gmail connection changed before session creation")
            self.sessions[secret_hash(token)] = record

    def get_session(self, token: str) -> SessionRecord | None:
        with self._lock:
            record = self.sessions.get(secret_hash(token))
        return record if record and self._alive(record.expires_at) else None

    def delete_session(self, token: str) -> None:
        with self._lock:
            self.sessions.pop(secret_hash(token), None)

    def put_user(self, user: UserRecord) -> None:
        with self._lock:
            self.users[user.user_id] = user

    def upsert_user_connection(
        self,
        user_id: str,
        email: str,
        encrypted_grant: str,
        now: int,
        consent_version: str,
        consented_at: int,
        connection_version: str,
        expected_connection_version: str | None,
    ) -> UserRecord:
        with self._lock:
            current = self.users.get(user_id)
            if expected_connection_version is not None and (
                current is None
                or current.connection_version != expected_connection_version
            ):
                raise RuntimeError("Gmail connection changed during authorization")
            if current:
                user = replace(
                    current,
                    email=email,
                    encrypted_grant=encrypted_grant,
                    updated_at=now,
                    consent_version=consent_version,
                    consented_at=consented_at,
                    connection_version=connection_version,
                    schedule_paused_for_consent=False,
                )
            else:
                user = UserRecord(
                    user_id=user_id,
                    email=email,
                    encrypted_grant=encrypted_grant,
                    created_at=now,
                    updated_at=now,
                    consent_version=consent_version,
                    consented_at=consented_at,
                    connection_version=connection_version,
                )
            self.users[user_id] = user
        return user

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._lock:
            return self.users.get(user_id)

    def save_policy(
        self,
        user_id: str,
        connection_version: str,
        policy: ClassificationPolicy,
    ) -> UserRecord:
        with self._lock:
            current = self.users[user_id]
            if current.connection_version != connection_version:
                raise RuntimeError("Gmail connection changed before policy save")
            if (
                policy.automatic_enabled
                and current.last_previewed_activation_hash != policy.activation_hash
            ):
                raise ValueError(
                    "Run a successful preview of this exact policy before enabling automation"
                )
            updated = replace(
                current,
                policy=policy,
                updated_at=int(time.time()),
                next_run_at=0 if policy.automatic_enabled else current.next_run_at,
            )
            self.users[user_id] = updated
            revision = PolicyRevision(
                policy_hash=policy.policy_hash,
                saved_at=int(time.time()),
                policy_json=policy.model_dump_json(),
                connection_version=connection_version,
            )
            self.policy_revisions[(user_id, revision.saved_at, policy.policy_hash)] = revision
        return updated

    def list_policy_revisions(
        self, user_id: str, connection_version: str, limit: int = 20
    ) -> list[PolicyRevision]:
        with self._lock:
            records = [
                revision
                for (owner, _, _), revision in self.policy_revisions.items()
                if owner == user_id and revision.connection_version == connection_version
            ]
        records.sort(key=lambda revision: revision.saved_at, reverse=True)
        return records[:limit]

    def mark_policy_previewed(
        self,
        user_id: str,
        activation_hash: str,
        connection_version: str,
    ) -> bool:
        with self._lock:
            current = self.users.get(user_id)
            if (
                current is None
                or current.policy is None
                or current.policy.activation_hash != activation_hash
                or current.connection_version != connection_version
            ):
                return False
            self.users[user_id] = replace(
                current,
                last_previewed_activation_hash=activation_hash,
                updated_at=int(time.time()),
            )
        return True

    def update_consent(
        self,
        user_id: str,
        connection_version: str,
        consent_version: str,
        consented_at: int,
        automatic_enabled: bool,
        policy_json: str,
    ) -> UserRecord:
        with self._lock:
            current = self.users.get(user_id)
            if current is None or current.connection_version != connection_version:
                raise RuntimeError("Gmail connection changed before consent update")
            if bool(current.policy and current.policy.automatic_enabled) != automatic_enabled:
                raise RuntimeError("Policy changed before consent update")
            if (current.policy.model_dump_json() if current.policy else "") != policy_json:
                raise RuntimeError("Policy changed before consent update")
            updated = replace(
                current,
                consent_version=consent_version,
                consented_at=consented_at,
                updated_at=consented_at,
                next_run_at=0 if automatic_enabled else current.next_run_at,
                schedule_paused_for_consent=False,
            )
            self.users[user_id] = updated
        return updated

    def list_automatic_users(
        self,
        limit: int = 100,
        due_before: int | None = None,
    ) -> list[ScheduledUserRecord]:
        due_before = int(time.time()) if due_before is None else due_before
        with self._lock:
            users = [
                user
                for user in self.users.values()
                if user.policy
                and user.policy.automatic_enabled
                and not user.schedule_paused_for_consent
                and user.next_run_at <= due_before
            ]
        users.sort(key=lambda user: (user.next_run_at, user.user_id))
        return [
            ScheduledUserRecord(
                user_id=user.user_id,
                policy_hash=user.policy.policy_hash,
                next_run_at=user.next_run_at,
                consent_version=user.consent_version,
                connection_version=user.connection_version,
            )
            for user in users[:limit]
            if user.policy is not None
        ]

    def claim_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        new_next_run_at: int,
        connection_version: str,
    ) -> bool:
        with self._lock:
            current = self.users.get(user_id)
            if (
                current is None
                or current.policy is None
                or not current.policy.automatic_enabled
                or current.next_run_at != expected_next_run_at
                or current.connection_version != connection_version
            ):
                return False
            self.users[user_id] = replace(current, next_run_at=new_next_run_at)
        return True

    def pause_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        connection_version: str,
        consent_version: str,
    ) -> bool:
        with self._lock:
            current = self.users.get(user_id)
            if (
                current is None
                or current.connection_version != connection_version
                or current.next_run_at != expected_next_run_at
                or current.consent_version != consent_version
            ):
                return False
            self.users[user_id] = replace(
                current,
                schedule_paused_for_consent=True,
                updated_at=int(time.time()),
            )
        return True

    def delete_user(self, user_id: str, connection_version: str) -> None:
        with self._lock:
            current = self.users.get(user_id)
            if current is None or current.connection_version != connection_version:
                return
            self.users.pop(user_id, None)
            for key in [key for key in self.runs if key[0] == user_id]:
                self.runs.pop(key, None)
            for key in [key for key in self.policy_revisions if key[0] == user_id]:
                self.policy_revisions.pop(key, None)
            for key, plan in list(self.plans.items()):
                if plan.user_id == user_id:
                    self.plans.pop(key, None)
            for key, session in list(self.sessions.items()):
                if session.user_id == user_id:
                    self.sessions.pop(key, None)
            self.active_runs.pop(user_id, None)
            for key in [key for key in self.quotas if key[0] == user_id]:
                self.quotas.pop(key, None)

    def acquire_active_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        expires_at: int,
        connection_version: str,
    ) -> bool:
        with self._lock:
            user = self.users.get(user_id)
            if user is None or user.connection_version != connection_version:
                return False
            current = self.active_runs.get(user_id)
            if current and current[1] > now and current[2] == connection_version:
                return False
            if user_id not in self.users:
                return False
            self.active_runs[user_id] = (run_id, expires_at, connection_version)
        return True

    def release_active_run(self, user_id: str, run_id: str) -> None:
        with self._lock:
            current = self.active_runs.get(user_id)
            if current and current[0] == run_id:
                self.active_runs.pop(user_id, None)

    def consume_quota(
        self,
        user_id: str,
        connection_version: str,
        quota_name: str,
        limit: int,
        window_seconds: int,
        now: int,
    ) -> bool:
        bucket = now // window_seconds
        key = (user_id, quota_name, bucket)
        with self._lock:
            user = self.users.get(user_id)
            if (
                user is None
                or user.connection_version != connection_version
                or self.quotas.get(key, 0) >= limit
            ):
                return False
            self.quotas[key] = self.quotas.get(key, 0) + 1
        return True

    def put_plan(self, plan_id: str, plan: ActionPlan) -> None:
        with self._lock:
            user = self.users.get(plan.user_id)
            if user is None or user.connection_version != plan.connection_version:
                raise RuntimeError("Gmail connection changed before plan creation")
            self.plans[secret_hash(plan_id)] = plan

    def get_plan(self, plan_id: str) -> ActionPlan | None:
        with self._lock:
            plan = self.plans.get(secret_hash(plan_id))
        return plan if plan and self._alive(plan.expires_at) else None

    def consume_plan(self, plan_id: str) -> ActionPlan | None:
        with self._lock:
            plan = self.plans.pop(secret_hash(plan_id), None)
        return plan if plan and self._alive(plan.expires_at) else None

    def put_run(self, run: RunRecord) -> None:
        with self._lock:
            user = self.users.get(run.user_id)
            if user is None or user.connection_version != run.connection_version:
                raise RuntimeError("Gmail connection changed before run creation")
            self.runs[(run.user_id, run.run_id)] = run

    def update_run(self, run: RunRecord) -> bool:
        with self._lock:
            key = (run.user_id, run.run_id)
            if key not in self.runs or run.user_id not in self.users:
                return False
            self.runs[key] = run
        return True

    def claim_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        lease_expires_at: int,
    ) -> RunRecord | None:
        with self._lock:
            key = (user_id, run_id)
            current = self.runs.get(key)
            if current is None or not (
                current.status in {"queued", "retry"}
                or (current.status == "running" and current.lease_expires_at <= now)
            ):
                return None
            claimed = replace(
                current,
                status="running",
                updated_at=now,
                lease_expires_at=lease_expires_at,
                attempts=current.attempts + 1,
            )
            self.runs[key] = claimed
        return claimed

    def get_run(self, user_id: str, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self.runs.get((user_id, run_id))
        return run if run and self._alive(run.expires_at) else None

    def list_runs(self, user_id: str, limit: int = 10) -> list[RunRecord]:
        with self._lock:
            records = [
                run
                for (owner, _), run in self.runs.items()
                if owner == user_id and self._alive(run.expires_at)
            ]
        records.sort(key=lambda run: run.created_at, reverse=True)
        return records[:limit]


class DynamoDbMultiTenantStore:
    def __init__(
        self,
        table_name: str,
        *,
        region_name: str | None = None,
        table: Any = None,
    ) -> None:
        if not table_name:
            raise ValueError("A DynamoDB table name is required")
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self._table = table

    def _transact_put_for_connection(
        self,
        item: dict[str, Any],
        user_id: str,
        connection_version: str,
    ) -> None:
        from boto3.dynamodb.types import TypeSerializer
        from botocore.exceptions import ClientError

        serializer = TypeSerializer()
        serialize = serializer.serialize
        try:
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self._table.name,
                            "Key": {
                                "pk": serialize(f"USER#{user_id}"),
                                "sk": serialize("PROFILE"),
                            },
                            "ConditionExpression": (
                                "entity_type = :user_entity AND "
                                "connection_version = :connection"
                            ),
                            "ExpressionAttributeValues": {
                                ":connection": serialize(connection_version),
                                ":user_entity": serialize("USER"),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {
                                key: serialize(value) for key, value in item.items()
                            },
                        }
                    },
                ]
            )
        except ClientError as exc:
            if _expected_transaction_condition(exc):
                raise RuntimeError(
                    "Gmail connection changed before tenant data was stored"
                ) from None
            raise

    @staticmethod
    def _delete_returned(table: Any, pk: str, sk: str) -> dict[str, Any] | None:
        response = table.delete_item(
            Key={"pk": pk, "sk": sk},
            ReturnValues="ALL_OLD",
        )
        return response.get("Attributes")

    @staticmethod
    def _not_expired(item: dict[str, Any] | None) -> bool:
        return bool(item and int(item.get("ttl") or 0) > int(time.time()))

    def put_oauth_state(self, state: str, record: OAuthStateRecord) -> None:
        digest = secret_hash(state)
        item = {
            "pk": f"STATE#{digest}",
            "sk": "STATE",
            "entity_type": "STATE",
            "code_verifier": record.code_verifier,
            "initiating_user_id": record.initiating_user_id or "",
            "browser_nonce_hash": record.browser_nonce_hash,
            "id_token_nonce": record.id_token_nonce,
            "consent_version": record.consent_version,
            "consented_at": record.consented_at,
            "purpose": record.purpose,
            "connection_version": record.initiating_connection_version,
            "ttl": record.expires_at,
        }
        if record.initiating_user_id:
            item["owner_key"] = f"USER#{record.initiating_user_id}"
        self._table.put_item(Item=item)

    def consume_oauth_state(self, state: str) -> OAuthStateRecord | None:
        digest = secret_hash(state)
        item = self._delete_returned(self._table, f"STATE#{digest}", "STATE")
        if not self._not_expired(item):
            return None
        return OAuthStateRecord(
            code_verifier=str(item["code_verifier"]),
            expires_at=int(item["ttl"]),
            initiating_user_id=str(item.get("initiating_user_id") or "") or None,
            browser_nonce_hash=str(item.get("browser_nonce_hash") or ""),
            id_token_nonce=str(item.get("id_token_nonce") or ""),
            consent_version=str(item.get("consent_version") or ""),
            consented_at=int(item.get("consented_at") or 0),
            purpose=str(item.get("purpose") or "connect"),
            initiating_connection_version=str(item.get("connection_version") or ""),
        )

    def put_session(self, token: str, record: SessionRecord) -> None:
        digest = secret_hash(token)
        self._transact_put_for_connection(
            {
                "pk": f"SESSION#{digest}",
                "sk": "SESSION",
                "entity_type": "SESSION",
                "user_id": record.user_id,
                "owner_key": f"USER#{record.user_id}",
                "csrf_token": record.csrf_token,
                "connection_version": record.connection_version,
                "ttl": record.expires_at,
            },
            record.user_id,
            record.connection_version,
        )

    def get_session(self, token: str) -> SessionRecord | None:
        digest = secret_hash(token)
        item = self._table.get_item(
            Key={"pk": f"SESSION#{digest}", "sk": "SESSION"},
            ConsistentRead=True,
        ).get("Item")
        if not self._not_expired(item):
            return None
        return SessionRecord(
            user_id=str(item["user_id"]),
            csrf_token=str(item["csrf_token"]),
            expires_at=int(item["ttl"]),
            connection_version=str(item.get("connection_version") or ""),
        )

    def delete_session(self, token: str) -> None:
        digest = secret_hash(token)
        self._table.delete_item(Key={"pk": f"SESSION#{digest}", "sk": "SESSION"})

    @staticmethod
    def _user_from_item(item: dict[str, Any] | None) -> UserRecord | None:
        if not item or item.get("entity_type") != "USER":
            return None
        policy_raw = item.get("policy_json")
        policy = ClassificationPolicy.model_validate_json(policy_raw) if policy_raw else None
        return UserRecord(
            user_id=str(item["user_id"]),
            email=str(item["email"]),
            encrypted_grant=str(item["encrypted_grant"]),
            policy=policy,
            created_at=int(item.get("created_at") or 0),
            updated_at=int(item.get("updated_at") or 0),
            next_run_at=int(item.get("next_run_at") or 0),
            last_previewed_activation_hash=str(
                item.get("last_previewed_activation_hash") or ""
            ),
            consent_version=str(item.get("consent_version") or ""),
            consented_at=int(item.get("consented_at") or 0),
            connection_version=str(item.get("connection_version") or ""),
            schedule_paused_for_consent=(
                str(item.get("schedule_key") or "") == "CONSENT_REQUIRED"
            ),
        )

    def put_user(self, user: UserRecord) -> None:
        self._table.put_item(
            Item={
                "pk": f"USER#{user.user_id}",
                "sk": "PROFILE",
                "entity_type": "USER",
                "schedule_key": (
                    "CONSENT_REQUIRED"
                    if user.schedule_paused_for_consent
                    else (
                        "AUTOMATIC"
                        if user.policy and user.policy.automatic_enabled
                        else "DISABLED"
                    )
                ),
                "user_id": user.user_id,
                "email": user.email,
                "encrypted_grant": user.encrypted_grant,
                "policy_json": user.policy.model_dump_json() if user.policy else "",
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "next_run_at": user.next_run_at,
                "policy_hash": user.policy.policy_hash if user.policy else "",
                "activation_hash": user.policy.activation_hash if user.policy else "",
                "last_previewed_activation_hash": user.last_previewed_activation_hash,
                "consent_version": user.consent_version,
                "consented_at": user.consented_at,
                "connection_version": user.connection_version,
            }
        )

    def upsert_user_connection(
        self,
        user_id: str,
        email: str,
        encrypted_grant: str,
        now: int,
        consent_version: str,
        consented_at: int,
        connection_version: str,
        expected_connection_version: str | None,
    ) -> UserRecord:
        current = self.get_user(user_id)
        if expected_connection_version is not None and (
            current is None
            or current.connection_version != expected_connection_version
        ):
            raise RuntimeError("Gmail connection changed during authorization")
        desired_schedule = (
            "AUTOMATIC"
            if current and current.policy and current.policy.automatic_enabled
            else "DISABLED"
        )
        kwargs: dict[str, Any] = {
            "Key": {"pk": f"USER#{user_id}", "sk": "PROFILE"},
            "UpdateExpression": (
                "SET entity_type = :entity_type, user_id = :user_id, email = :email, "
                "encrypted_grant = :grant, updated_at = :updated, "
                "consent_version = :consent_version, consented_at = :consented_at, "
                "connection_version = :connection, "
                "created_at = if_not_exists(created_at, :created), "
                "schedule_key = :schedule, next_run_at = :next_run, "
                "policy_hash = if_not_exists(policy_hash, :empty), "
                "activation_hash = if_not_exists(activation_hash, :empty), "
                "last_previewed_activation_hash = "
                "if_not_exists(last_previewed_activation_hash, :empty)"
            ),
            "ExpressionAttributeValues": {
                ":entity_type": "USER",
                ":user_id": user_id,
                ":email": email,
                ":grant": encrypted_grant,
                ":updated": now,
                ":consent_version": consent_version,
                ":consented_at": consented_at,
                ":connection": connection_version,
                ":created": now,
                ":schedule": desired_schedule,
                ":next_run": 0 if desired_schedule == "AUTOMATIC" else (
                    current.next_run_at if current else 0
                ),
                ":empty": "",
            },
            "ReturnValues": "ALL_NEW",
        }
        if current is not None:
            kwargs["ConditionExpression"] = (
                "entity_type = :entity_type AND "
                "connection_version = :expected_connection AND "
                "policy_json = :expected_policy"
            )
            kwargs["ExpressionAttributeValues"][
                ":expected_connection"
            ] = current.connection_version
            kwargs["ExpressionAttributeValues"][":expected_policy"] = (
                current.policy.model_dump_json() if current.policy else ""
            )
        else:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            response = self._table.update_item(**kwargs)
        except Exception as exc:
            if (
                getattr(exc, "response", {}).get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise RuntimeError("Gmail connection changed during authorization") from None
            raise
        user = self._user_from_item(response.get("Attributes"))
        if user is None:
            raise RuntimeError("DynamoDB did not return the connected user")
        return user

    def get_user(self, user_id: str) -> UserRecord | None:
        item = self._table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
            ConsistentRead=True,
        ).get("Item")
        return self._user_from_item(item)

    def save_policy(
        self,
        user_id: str,
        connection_version: str,
        policy: ClassificationPolicy,
    ) -> UserRecord:
        from botocore.exceptions import ClientError

        now = int(time.time())
        try:
            response = self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
                UpdateExpression=(
                    "SET policy_json = :policy, schedule_key = :schedule, "
                    "policy_hash = :policy_hash, activation_hash = :activation_hash, "
                    "next_run_at = :next_run, updated_at = :updated"
                ),
                ConditionExpression=(
                    "entity_type = :user_entity AND "
                    "last_previewed_activation_hash = :activation_hash"
                    " AND connection_version = :connection"
                    if policy.automatic_enabled
                    else (
                        "entity_type = :user_entity AND "
                        "connection_version = :connection"
                    )
                ),
                ExpressionAttributeValues={
                    ":policy": policy.model_dump_json(),
                    ":schedule": (
                        "AUTOMATIC" if policy.automatic_enabled else "DISABLED"
                    ),
                    ":policy_hash": policy.policy_hash,
                    ":activation_hash": policy.activation_hash,
                    ":next_run": 0,
                    ":updated": now,
                    ":connection": connection_version,
                    ":user_entity": "USER",
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                if policy.automatic_enabled:
                    raise ValueError(
                        "Run a successful preview of this exact policy before enabling automation"
                    ) from None
                raise RuntimeError("Gmail connection changed before policy save") from None
            raise
        user = self._user_from_item(response.get("Attributes"))
        if user is None:
            raise RuntimeError("DynamoDB did not return the updated user")
        self._table.put_item(
            Item={
                "pk": f"USER#{user_id}",
                "sk": f"POLICY#{now:010d}#{policy.policy_hash}",
                "entity_type": "POLICY_REVISION",
                "user_id": user_id,
                "policy_hash": policy.policy_hash,
                "policy_json": policy.model_dump_json(),
                "saved_at": now,
                "connection_version": connection_version,
                "ttl": now + 31_536_000,
            }
        )
        return user

    @staticmethod
    def _policy_revision_from_item(item: dict[str, Any] | None) -> PolicyRevision | None:
        if not item or int(item.get("ttl") or 0) <= int(time.time()):
            return None
        return PolicyRevision(
            policy_hash=str(item["policy_hash"]),
            saved_at=int(item["saved_at"]),
            policy_json=str(item["policy_json"]),
            connection_version=str(item.get("connection_version") or ""),
        )

    def list_policy_revisions(
        self, user_id: str, connection_version: str, limit: int = 20
    ) -> list[PolicyRevision]:
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            KeyConditionExpression=(
                Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("POLICY#")
            ),
            Limit=limit,
            ScanIndexForward=False,
        )
        return [
            revision
            for item in response.get("Items") or []
            if (revision := self._policy_revision_from_item(item)) is not None
            and revision.connection_version == connection_version
        ]

    def mark_policy_previewed(
        self,
        user_id: str,
        activation_hash: str,
        connection_version: str,
    ) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
                UpdateExpression=(
                    "SET last_previewed_activation_hash = :activation_hash, "
                    "updated_at = :updated"
                ),
                ConditionExpression=(
                    "activation_hash = :activation_hash AND "
                    "entity_type = :user_entity AND "
                    "connection_version = :connection"
                ),
                ExpressionAttributeValues={
                    ":activation_hash": activation_hash,
                    ":connection": connection_version,
                    ":user_entity": "USER",
                    ":updated": int(time.time()),
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def update_consent(
        self,
        user_id: str,
        connection_version: str,
        consent_version: str,
        consented_at: int,
        automatic_enabled: bool,
        policy_json: str,
    ) -> UserRecord:
        from botocore.exceptions import ClientError

        try:
            response = self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
                UpdateExpression=(
                    "SET consent_version = :consent, consented_at = :consented_at, "
                    "updated_at = :consented_at, schedule_key = :schedule, "
                    "next_run_at = :next_run"
                ),
                ConditionExpression=(
                    "entity_type = :user_entity AND "
                    "connection_version = :connection AND "
                    "policy_json = :policy_json"
                ),
                ExpressionAttributeValues={
                    ":consent": consent_version,
                    ":consented_at": consented_at,
                    ":schedule": "AUTOMATIC" if automatic_enabled else "DISABLED",
                    ":next_run": 0,
                    ":connection": connection_version,
                    ":policy_json": policy_json,
                    ":user_entity": "USER",
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise RuntimeError("Gmail connection or consent state changed") from None
            raise
        user = self._user_from_item(response.get("Attributes"))
        if user is None:
            raise RuntimeError("DynamoDB did not return the consented user")
        if bool(user.policy and user.policy.automatic_enabled) != automatic_enabled:
            raise RuntimeError("Policy changed before consent update")
        return user

    def list_automatic_users(
        self,
        limit: int = 100,
        due_before: int | None = None,
    ) -> list[ScheduledUserRecord]:
        from boto3.dynamodb.conditions import Key

        due_before = int(time.time()) if due_before is None else due_before
        response = self._table.query(
            IndexName="ScheduleIndex",
            KeyConditionExpression=(
                Key("schedule_key").eq("AUTOMATIC")
                & Key("next_run_at").lte(due_before)
            ),
            Limit=limit,
        )
        users: list[ScheduledUserRecord] = []
        for item in response.get("Items") or []:
            policy_hash = str(item.get("policy_hash") or "")
            if not policy_hash:
                continue
            users.append(
                ScheduledUserRecord(
                    user_id=str(item.get("user_id") or str(item["pk"])[5:]),
                    policy_hash=policy_hash,
                    next_run_at=int(item.get("next_run_at") or 0),
                    consent_version=str(item.get("consent_version") or ""),
                    connection_version=str(item.get("connection_version") or ""),
                )
            )
        return users

    def claim_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        new_next_run_at: int,
        connection_version: str,
    ) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
                UpdateExpression="SET next_run_at = :new_next",
                ConditionExpression=(
                    "schedule_key = :automatic AND next_run_at = :expected_next AND "
                    "entity_type = :user_entity AND connection_version = :connection"
                ),
                ExpressionAttributeValues={
                    ":automatic": "AUTOMATIC",
                    ":expected_next": expected_next_run_at,
                    ":new_next": new_next_run_at,
                    ":connection": connection_version,
                    ":user_entity": "USER",
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def pause_automatic_user(
        self,
        user_id: str,
        expected_next_run_at: int,
        connection_version: str,
        consent_version: str,
    ) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": "PROFILE"},
                UpdateExpression="SET schedule_key = :paused, updated_at = :updated",
                ConditionExpression=(
                    "schedule_key = :automatic AND next_run_at = :expected_next AND "
                    "entity_type = :user_entity AND connection_version = :connection AND "
                    "consent_version = :consent"
                ),
                ExpressionAttributeValues={
                    ":paused": "CONSENT_REQUIRED",
                    ":updated": int(time.time()),
                    ":automatic": "AUTOMATIC",
                    ":expected_next": expected_next_run_at,
                    ":connection": connection_version,
                    ":consent": consent_version,
                    ":user_entity": "USER",
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def delete_user(self, user_id: str, connection_version: str) -> None:
        from boto3.dynamodb.conditions import Key
        from botocore.exceptions import ClientError

        profile_key = {"pk": f"USER#{user_id}", "sk": "PROFILE"}
        profile = self._table.get_item(Key=profile_key, ConsistentRead=True).get("Item")
        if profile and str(profile.get("connection_version") or "") != connection_version:
            return
        tombstoned = bool(profile and profile.get("entity_type") == "DELETING")
        if profile and not tombstoned:
            try:
                self._table.update_item(
                    Key=profile_key,
                    UpdateExpression=(
                        "SET entity_type = :deleting, schedule_key = :disabled, "
                        "updated_at = :updated, #ttl = :ttl "
                        "REMOVE email, encrypted_grant, policy_json, policy_hash, "
                        "activation_hash, last_previewed_activation_hash, "
                        "consent_version, consented_at, next_run_at"
                    ),
                    ConditionExpression=(
                        "entity_type = :user_entity AND "
                        "connection_version = :connection"
                    ),
                    ExpressionAttributeNames={"#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":deleting": "DELETING",
                        ":disabled": "DISABLED",
                        ":updated": int(time.time()),
                        ":ttl": int(time.time()) + 86_400,
                        ":user_entity": "USER",
                        ":connection": connection_version,
                    },
                )
                tombstoned = True
            except ClientError as exc:
                if (
                    exc.response.get("Error", {}).get("Code")
                    != "ConditionalCheckFailedException"
                ):
                    raise
                current = self._table.get_item(
                    Key=profile_key,
                    ConsistentRead=True,
                ).get("Item")
                if not current:
                    profile = None
                elif (
                    current.get("entity_type") == "DELETING"
                    and str(current.get("connection_version") or "")
                    == connection_version
                ):
                    tombstoned = True
                else:
                    return

        # Once the tombstone exists, every operation guard and conditional child write
        # fails immediately. Cleanup is then safe to retry and cannot delete a newly
        # reconnected generation. If cleanup is interrupted, TTL removes inaccessible
        # children and the tombstone while an alarm directs the operator to retry it.
        keys: dict[tuple[str, str], dict[str, str]] = {}

        def collect_query(**kwargs: Any) -> None:
            cursor: dict[str, Any] | None = None
            while True:
                if cursor:
                    kwargs["ExclusiveStartKey"] = cursor
                response = self._table.query(**kwargs)
                for item in response.get("Items") or []:
                    if str(item.get("connection_version") or "") != connection_version:
                        continue
                    key = {"pk": str(item["pk"]), "sk": str(item["sk"])}
                    keys[(key["pk"], key["sk"])] = key
                cursor = response.get("LastEvaluatedKey")
                if not cursor:
                    break

        try:
            collect_query(
                KeyConditionExpression=Key("pk").eq(f"USER#{user_id}"),
                ProjectionExpression="pk, sk, connection_version",
                ConsistentRead=True,
            )
            collect_query(
                IndexName="OwnerIndex",
                KeyConditionExpression=Key("owner_key").eq(f"USER#{user_id}"),
                ProjectionExpression="pk, sk, connection_version",
            )
            keys.pop((profile_key["pk"], profile_key["sk"]), None)
            for key in keys.values():
                try:
                    self._table.delete_item(
                        Key=key,
                        ConditionExpression="connection_version = :connection",
                        ExpressionAttributeValues={":connection": connection_version},
                    )
                except ClientError as exc:
                    if (
                        exc.response.get("Error", {}).get("Code")
                        != "ConditionalCheckFailedException"
                    ):
                        raise
            if tombstoned:
                self._table.delete_item(
                    Key=profile_key,
                    ConditionExpression=(
                        "entity_type = :deleting AND connection_version = :connection"
                    ),
                    ExpressionAttributeValues={
                        ":deleting": "DELETING",
                        ":connection": connection_version,
                    },
                )
        except Exception:
            if not tombstoned:
                raise
            LOGGER.exception("Deletion cleanup incomplete for user reference")

    def acquire_active_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        expires_at: int,
        connection_version: str,
    ) -> bool:
        from boto3.dynamodb.types import TypeSerializer
        from botocore.exceptions import ClientError

        serializer = TypeSerializer()
        serialize = lambda value: serializer.serialize(value)  # noqa: E731
        table_name = self._table.name
        try:
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": table_name,
                            "Key": {
                                "pk": serialize(f"USER#{user_id}"),
                                "sk": serialize("PROFILE"),
                            },
                            "ConditionExpression": (
                                "entity_type = :user_entity AND "
                                "connection_version = :connection"
                            ),
                            "ExpressionAttributeValues": {
                                ":connection": serialize(connection_version),
                                ":user_entity": serialize("USER"),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": table_name,
                            "Item": {
                                "pk": serialize(f"USER#{user_id}"),
                                "sk": serialize("ACTIVE_RUN"),
                                "entity_type": serialize("ACTIVE_RUN"),
                                "run_id": serialize(run_id),
                                "connection_version": serialize(connection_version),
                                "ttl": serialize(expires_at),
                            },
                            "ConditionExpression": (
                                "attribute_not_exists(pk) OR "
                                "connection_version <> :connection OR #ttl <= :now"
                            ),
                            "ExpressionAttributeNames": {"#ttl": "ttl"},
                            "ExpressionAttributeValues": {
                                ":now": serialize(now),
                                ":connection": serialize(connection_version),
                            },
                        }
                    },
                ]
            )
        except ClientError as exc:
            if _expected_transaction_condition(exc):
                return False
            raise
        return True

    def release_active_run(self, user_id: str, run_id: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._table.delete_item(
                Key={"pk": f"USER#{user_id}", "sk": "ACTIVE_RUN"},
                ConditionExpression="run_id = :run_id",
                ExpressionAttributeValues={":run_id": run_id},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def consume_quota(
        self,
        user_id: str,
        connection_version: str,
        quota_name: str,
        limit: int,
        window_seconds: int,
        now: int,
    ) -> bool:
        from botocore.exceptions import ClientError

        bucket = now // window_seconds
        from boto3.dynamodb.types import TypeSerializer

        serializer = TypeSerializer()
        serialize = serializer.serialize
        try:
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self._table.name,
                            "Key": {
                                "pk": serialize(f"USER#{user_id}"),
                                "sk": serialize("PROFILE"),
                            },
                            "ConditionExpression": (
                                "entity_type = :user_entity AND "
                                "connection_version = :connection"
                            ),
                            "ExpressionAttributeValues": {
                                ":connection": serialize(connection_version),
                                ":user_entity": serialize("USER"),
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {
                                "pk": serialize(f"USER#{user_id}"),
                                "sk": serialize(f"QUOTA#{quota_name}#{bucket}"),
                            },
                            "UpdateExpression": (
                                "SET entity_type = :entity_type, #ttl = :ttl, "
                                "connection_version = :connection ADD #count :one"
                            ),
                            "ConditionExpression": (
                                "attribute_not_exists(#count) OR #count < :limit"
                            ),
                            "ExpressionAttributeNames": {"#count": "count", "#ttl": "ttl"},
                            "ExpressionAttributeValues": {
                                ":entity_type": serialize("QUOTA"),
                                ":ttl": serialize((bucket + 2) * window_seconds),
                                ":connection": serialize(connection_version),
                                ":one": serialize(1),
                                ":limit": serialize(limit),
                            },
                        }
                    },
                ]
            )
        except ClientError as exc:
            if _expected_transaction_condition(exc):
                return False
            raise
        return True

    def put_plan(self, plan_id: str, plan: ActionPlan) -> None:
        digest = secret_hash(plan_id)
        self._transact_put_for_connection(
            {
                "pk": f"PLAN#{digest}",
                "sk": "PLAN",
                "entity_type": "PLAN",
                "user_id": plan.user_id,
                "owner_key": f"USER#{plan.user_id}",
                "policy_hash": plan.policy_hash,
                "mailbox": plan.mailbox,
                "outcomes_json": json.dumps(
                    [outcome.as_dict() for outcome in plan.outcomes],
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                "connection_version": plan.connection_version,
                "ttl": plan.expires_at,
            },
            plan.user_id,
            plan.connection_version,
        )

    def _plan_from_item(self, item: dict[str, Any] | None) -> ActionPlan | None:
        if not self._not_expired(item):
            return None
        outcomes = tuple(
            UniversalOutcome(
                message_id=str(value["message_id"]),
                thread_id=str(value.get("thread_id") or ""),
                subject=str(value.get("subject") or ""),
                sender=str(value.get("sender") or ""),
                proposed_label=value.get("proposed_label"),
                secondary_label=value.get("secondary_label"),
                confidence=float(value.get("confidence") or 0.0),
                action=str(value.get("action") or ""),
                reason=str(value.get("reason") or ""),
                evidence=tuple(value.get("evidence") or []),
            )
            for value in json.loads(str(item["outcomes_json"]))
        )
        return ActionPlan(
            user_id=str(item["user_id"]),
            policy_hash=str(item["policy_hash"]),
            mailbox=str(item["mailbox"]),
            outcomes=outcomes,
            expires_at=int(item["ttl"]),
            connection_version=str(item.get("connection_version") or ""),
        )

    def get_plan(self, plan_id: str) -> ActionPlan | None:
        digest = secret_hash(plan_id)
        item = self._table.get_item(
            Key={"pk": f"PLAN#{digest}", "sk": "PLAN"},
            ConsistentRead=True,
        ).get("Item")
        return self._plan_from_item(item)

    def consume_plan(self, plan_id: str) -> ActionPlan | None:
        digest = secret_hash(plan_id)
        item = self._delete_returned(self._table, f"PLAN#{digest}", "PLAN")
        return self._plan_from_item(item)

    @staticmethod
    def _run_from_item(item: dict[str, Any] | None) -> RunRecord | None:
        if not item or int(item.get("ttl") or 0) <= int(time.time()):
            return None
        return RunRecord(
            run_id=str(item["run_id"]),
            user_id=str(item["user_id"]),
            mode=str(item["mode"]),
            status=str(item["status"]),
            policy_hash=str(item["policy_hash"]),
            created_at=int(item["created_at"]),
            updated_at=int(item["updated_at"]),
            expires_at=int(item.get("ttl") or 0),
            report_json=str(item.get("report_json") or ""),
            plan_id=str(item.get("plan_id") or ""),
            error=str(item.get("error") or ""),
            lease_expires_at=int(item.get("lease_expires_at") or 0),
            attempts=int(item.get("attempts") or 0),
            connection_version=str(item.get("connection_version") or ""),
        )

    def put_run(self, run: RunRecord) -> None:
        self._transact_put_for_connection(
            self._run_item(run),
            run.user_id,
            run.connection_version,
        )

    @staticmethod
    def _run_item(run: RunRecord) -> dict[str, Any]:
        return {
            "pk": f"USER#{run.user_id}",
            "sk": f"RUN#{run.run_id}",
            "entity_type": "RUN",
            "run_id": run.run_id,
            "user_id": run.user_id,
            "mode": run.mode,
            "status": run.status,
            "policy_hash": run.policy_hash,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "ttl": run.expires_at,
            "report_json": run.report_json,
            "plan_id": run.plan_id,
            "error": run.error,
            "lease_expires_at": run.lease_expires_at,
            "attempts": run.attempts,
            "connection_version": run.connection_version,
        }

    def update_run(self, run: RunRecord) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table.put_item(
                Item=self._run_item(run),
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def claim_run(
        self,
        user_id: str,
        run_id: str,
        now: int,
        lease_expires_at: int,
    ) -> RunRecord | None:
        from botocore.exceptions import ClientError

        try:
            response = self._table.update_item(
                Key={"pk": f"USER#{user_id}", "sk": f"RUN#{run_id}"},
                UpdateExpression=(
                    "SET #status = :running, updated_at = :updated, "
                    "lease_expires_at = :lease, "
                    "attempts = if_not_exists(attempts, :zero) + :one"
                ),
                ConditionExpression=(
                    "#status IN (:queued, :retry) OR "
                    "(#status = :running AND "
                    "(attribute_not_exists(lease_expires_at) OR lease_expires_at <= :updated))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":queued": "queued",
                    ":retry": "retry",
                    ":running": "running",
                    ":updated": now,
                    ":lease": lease_expires_at,
                    ":zero": 0,
                    ":one": 1,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return self._run_from_item(response.get("Attributes"))

    def get_run(self, user_id: str, run_id: str) -> RunRecord | None:
        item = self._table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": f"RUN#{run_id}"},
            ConsistentRead=True,
        ).get("Item")
        return self._run_from_item(item)

    def list_runs(self, user_id: str, limit: int = 10) -> list[RunRecord]:
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            KeyConditionExpression=(
                Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("RUN#")
            ),
            Limit=limit,
            ScanIndexForward=False,
        )
        return [
            run
            for item in response.get("Items") or []
            if (run := self._run_from_item(item)) is not None
        ]
