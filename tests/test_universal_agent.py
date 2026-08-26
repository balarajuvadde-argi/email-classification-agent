import base64
import time

import pytest

from email_classification_agent.property_appraiser import PropertyRecord
from email_classification_agent.universal_agent import UniversalClassificationAgent
from email_classification_agent.universal_models import (
    ActionPlan,
    ClassificationPolicy,
    UniversalDecision,
)


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _resource(message_id: str = "m1") -> dict:
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": ["INBOX"],
        "internalDate": "1000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Vendor <billing@example.com>"},
                {"name": "Subject", "value": "Invoice 123"},
            ],
            "body": {"data": _encoded("Please pay invoice 123")},
        },
    }


class _Gmail:
    def __init__(self, mailbox="user@example.com"):
        self.mailbox = mailbox
        self.writes = []
        self.ensure_calls = []

    def profile_email(self):
        return self.mailbox

    def ensure_label(self, name, visible, create=True):
        self.ensure_calls.append((name, visible, create))
        return f"DRYRUN:{name}" if not create else f"ID:{name}"

    def list_message_ids(self, query, max_results):
        self.query = query
        self.max_results = max_results
        return ["m1"]

    def get_message(self, message_id):
        return _resource(message_id)

    def get_thread(self, thread_id):
        return {"messages": [_resource("m1")]}

    def add_labels(self, message_id, label_ids, *, remove_inbox=False):
        self.writes.append((message_id, list(label_ids), remove_inbox))


class _Classifier:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def classify(self, *args, **kwargs):
        if args:
            self.calls.append(args[0])
        return self.decision


class _PropertyClient:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def lookup_email(self, message):
        self.calls.append(message)
        return list(self.records)


def _policy(threshold=0.85):
    return ClassificationPolicy(
        prompt="Label all invoices and payment receipts as Finance.",
        labels=["Finance", "News"],
        confidence_threshold=threshold,
    )


def _wholesale_policy():
    return ClassificationPolicy(
        prompt="Label actual wholesale property offers as Wholesaler.",
        labels=["Wholesaler"],
    )


def _wholesale_current_policy():
    return ClassificationPolicy(
        prompt="Label actual wholesale property offers as Wholesale.",
        labels=["Wholesale"],
    )


def _wholesale_resource(message_id="m1"):
    resource = _resource(message_id)
    resource["payload"] = {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "Vendor <billing@example.com>"},
            {"name": "Subject", "value": "Wholesale deal in 33131"},
        ],
        "body": {"data": _encoded("Property offer in Miami, FL 33131")},
    }
    return resource


def test_miami_dade_zip_adds_derived_sublabel_on_preview() -> None:
    gmail = _Gmail()
    gmail.get_message = _wholesale_resource
    report = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesaler",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
    ).preview(_wholesale_policy())

    assert report.outcomes[0].secondary_label == "Wholesaler/Miami-Dade"
    assert gmail.writes == []


def test_miami_dade_zip_and_sublabels_can_be_changed_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ACQUISITION_MIAMI_DADE_ZIPS", "99999")
    monkeypatch.setenv("ACQUISITION_MIAMI_DADE_LABEL_SUFFIX", "Target-County")
    gmail = _Gmail()
    resource = _wholesale_resource()
    resource["payload"]["headers"][1]["value"] = "Wholesale deal in 99999"
    resource["payload"]["body"]["data"] = _encoded("Property offer in test county 99999")
    gmail.get_message = lambda message_id: resource

    report = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesaler",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
    ).preview(_wholesale_policy())

    assert report.outcomes[0].secondary_label == "Wholesaler/Target-County"


def test_one_matching_zip_routes_email_with_other_non_miami_zips() -> None:
    gmail = _Gmail()
    resource = _wholesale_resource()
    resource["payload"]["headers"][1]["value"] = (
        "Wholesale portfolio: Lake Worth 33460, Miami-Dade 33034, Jacksonville 32218"
    )
    gmail.get_message = lambda message_id: resource

    report = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesaler",
                confidence=0.96,
                reason="Property portfolio",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
    ).preview(_wholesale_policy())

    assert report.outcomes[0].secondary_label == "Wholesaler/Miami-Dade"


def test_qualified_miami_dade_property_routes_to_important_sublabel() -> None:
    gmail = _Gmail()
    gmail.get_message = _wholesale_resource
    policy = _wholesale_current_policy()
    property_client = _PropertyClient(
        [
            PropertyRecord(
                address="16225 NE 2nd Ave",
                asking_price=250_000,
                folio="30-2218-007-2720",
                municipality="UNINCORPORATED COUNTY",
                land_use="RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                legal_description="LOT 12 AND 13",
                lot_size_sqft=10_000,
                lookup_status="verified",
                qualifies=True,
                is_folio_30=True,
                has_double_lot=True,
                is_unincorporated=True,
                reasons=("folio starts with 30 (unincorporated Miami-Dade)",),
            )
        ]
    )
    agent = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesale",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
        property_client=property_client,
    )

    preview = agent.preview(policy)
    plan = ActionPlan(
        user_id="u1",
        policy_hash=policy.policy_hash,
        mailbox="user@example.com",
        outcomes=tuple(preview.outcomes),
        expires_at=int(time.time()) + 60,
    )

    report = agent.apply_plan(policy, plan)

    assert preview.outcomes[0].secondary_label == "Wholesale/Miami-Dade/Important"
    assert report.outcomes[0].secondary_label == "Wholesale/Miami-Dade/Important"
    assert gmail.writes == [
        (
            "m1",
            [
                "ID:Wholesale/Miami-Dade/Important",
                "ID:Wholesale/Miami-Dade",
                "ID:Wholesale",
                f"ID:{policy.processed_label}",
            ],
            True,
        )
    ]
    assert len(property_client.calls) == 1
    assert all("Qualified" not in call[0] for call in gmail.ensure_calls)


def test_important_sublabel_can_be_changed_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ACQUISITION_IMPORTANT_LABEL_SUFFIX", "Miami-Dade/Hot")
    gmail = _Gmail()
    gmail.get_message = _wholesale_resource
    property_client = _PropertyClient(
        [
            PropertyRecord(
                address="16225 NE 2nd Ave",
                asking_price=250_000,
                folio="30-2218-007-2720",
                municipality="UNINCORPORATED COUNTY",
                land_use="RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                legal_description="LOTS 4 & 5",
                lot_size_sqft=10_000,
                lookup_status="verified",
                qualifies=True,
                is_folio_30=True,
                has_double_lot=True,
                is_unincorporated=True,
                reasons=("folio starts with 30 (unincorporated Miami-Dade)",),
            )
        ]
    )

    report = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesale",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
        property_client=property_client,
    ).preview(_wholesale_current_policy())

    assert report.outcomes[0].secondary_label == "Wholesale/Miami-Dade/Hot"


def test_property_lookup_only_runs_after_miami_dade_zip_match() -> None:
    gmail = _Gmail()
    resource = _wholesale_resource()
    resource["payload"]["headers"][1]["value"] = "Wholesale deal in Palm Beach"
    resource["payload"]["body"]["data"] = _encoded("Property offer in Lake Worth Beach 33460")
    gmail.get_message = lambda message_id: resource
    property_client = _PropertyClient(
        [
            PropertyRecord(
                address="16225 NE 2nd Ave",
                asking_price=250_000,
                folio="30-2218-007-2720",
                municipality="UNINCORPORATED COUNTY",
                land_use="RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                legal_description="LOT 12 AND 13",
                lot_size_sqft=10_000,
                lookup_status="verified",
                qualifies=True,
                is_folio_30=True,
                has_double_lot=True,
                is_unincorporated=True,
                reasons=("folio starts with 30 (unincorporated Miami-Dade)",),
            )
        ]
    )

    report = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesale",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
        property_client=property_client,
    ).preview(_wholesale_current_policy())

    assert report.outcomes[0].proposed_label == "Wholesale"
    assert report.outcomes[0].secondary_label is None
    assert report.outcomes[0].property_records == ()
    assert property_client.calls == []


def test_miami_dade_sublabel_is_added_during_apply() -> None:
    gmail = _Gmail()
    gmail.get_message = _wholesale_resource
    policy = _wholesale_policy()
    agent = UniversalClassificationAgent(
        gmail,
        _Classifier(
            UniversalDecision(
                label="Wholesaler",
                confidence=0.96,
                reason="Property offer",
                evidence=[],
            )
        ),
        expected_email="user@example.com",
    )
    preview = agent.preview(policy)
    plan = ActionPlan(
        user_id="u1",
        policy_hash=policy.policy_hash,
        mailbox="user@example.com",
        outcomes=tuple(preview.outcomes),
        expires_at=int(time.time()) + 60,
    )

    agent.apply_plan(policy, plan)

    assert gmail.writes == [
        (
            "m1",
            [
                "ID:Wholesaler/Miami-Dade",
                "ID:Wholesaler",
                f"ID:{policy.processed_label}",
            ],
            True,
        )
    ]


def test_preview_is_read_only_and_proposes_only_allowed_label() -> None:
    gmail = _Gmail()
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.96, reason="Invoice", evidence=[])
    )
    report = UniversalClassificationAgent(
        gmail, classifier, expected_email="user@example.com"
    ).preview(_policy())

    assert report.proposed == 1
    assert report.outcomes[0].proposed_label == "Finance"
    assert gmail.writes == []
    assert all(create is False for _, _, create in gmail.ensure_calls)
    assert '-label:"EmailAgent/Processed/' not in gmail.query


def test_automatic_run_skips_processed_messages() -> None:
    gmail = _Gmail()
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.96, reason="Invoice", evidence=[])
    )
    policy = ClassificationPolicy(
        prompt="Label all invoices and payment receipts as Finance.",
        labels=["Finance"],
        automatic_enabled=True,
    )

    UniversalClassificationAgent(
        gmail, classifier, expected_email="user@example.com"
    ).run_automatic(policy)

    assert '-label:"EmailAgent/Processed/' in gmail.query


def test_preview_processes_candidates_newest_first_even_when_gmail_ids_are_unordered() -> None:
    gmail = _Gmail()
    resources = {
        "old": _resource("old"),
        "new": _resource("new"),
        "middle": _resource("middle"),
    }
    resources["old"]["internalDate"] = "1000"
    resources["old"]["payload"]["headers"][1]["value"] = "Old invoice"
    resources["new"]["internalDate"] = "3000"
    resources["new"]["payload"]["headers"][1]["value"] = "New invoice"
    resources["middle"]["internalDate"] = "2000"
    resources["middle"]["payload"]["headers"][1]["value"] = "Middle invoice"
    gmail.list_message_ids = lambda query, max_results: ["old", "new", "middle"]
    gmail.get_message = lambda message_id: resources[message_id]
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.96, reason="Invoice", evidence=[])
    )
    policy = ClassificationPolicy(
        prompt="Label all invoices and payment receipts as Finance.",
        labels=["Finance"],
        max_messages_per_run=2,
    )

    report = UniversalClassificationAgent(
        gmail, classifier, expected_email="user@example.com"
    ).preview(policy)

    assert [outcome.message_id for outcome in report.outcomes] == ["new", "middle"]
    assert [message.message_id for message in classifier.calls] == ["new", "middle"]


def test_candidate_scan_window_can_be_changed_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("CANDIDATE_SCAN_WINDOW", "7")
    gmail = _Gmail()
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.96, reason="Invoice", evidence=[])
    )
    policy = ClassificationPolicy(
        prompt="Label all invoices and payment receipts as Finance.",
        labels=["Finance"],
        max_messages_per_run=2,
    )

    UniversalClassificationAgent(
        gmail, classifier, expected_email="user@example.com"
    ).preview(policy)

    assert gmail.max_results == 7


def test_apply_reviewed_plan_adds_destination_and_processed_labels_only() -> None:
    gmail = _Gmail()
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.96, reason="Invoice", evidence=[])
    )
    agent = UniversalClassificationAgent(gmail, classifier, expected_email="user@example.com")
    preview = agent.preview(_policy())
    plan = ActionPlan(
        user_id="u1",
        policy_hash=_policy().policy_hash,
        mailbox="user@example.com",
        outcomes=tuple(preview.outcomes),
        expires_at=int(time.time()) + 60,
    )

    report = agent.apply_plan(_policy(), plan)

    assert report.labeled == 1
    assert gmail.writes == [
        (
            "m1",
            ["ID:Finance", f"ID:{_policy().processed_label}"],
            True,
        )
    ]


def test_below_threshold_preview_is_not_marked_processed_when_plan_is_applied() -> None:
    gmail = _Gmail()
    classifier = _Classifier(
        UniversalDecision(label="Finance", confidence=0.6, reason="Maybe", evidence=[])
    )
    agent = UniversalClassificationAgent(gmail, classifier, expected_email="user@example.com")
    policy = _policy(0.9)
    preview = agent.preview(policy)
    plan = ActionPlan(
        user_id="u1",
        policy_hash=policy.policy_hash,
        mailbox="user@example.com",
        outcomes=tuple(preview.outcomes),
        expires_at=int(time.time()) + 60,
    )

    report = agent.apply_plan(policy, plan)

    assert report.low_confidence == 1
    assert gmail.writes == []


def test_tenant_mailbox_mismatch_stops_before_any_label_lookup() -> None:
    gmail = _Gmail(mailbox="other@example.com")
    classifier = _Classifier(
        UniversalDecision(label=None, confidence=1, reason="None", evidence=[])
    )

    with pytest.raises(RuntimeError, match="Tenant mailbox check failed"):
        UniversalClassificationAgent(
            gmail, classifier, expected_email="user@example.com"
        ).preview(_policy())

    assert gmail.ensure_calls == []
