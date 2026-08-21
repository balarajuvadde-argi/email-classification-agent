import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class _CfnLoader(yaml.SafeLoader):
    pass


def _construct_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _construct_tag)


def test_web_stack_has_multitenant_security_and_async_resources() -> None:
    template = yaml.load((ROOT / "template-web.yaml").read_text(), Loader=_CfnLoader)
    resources = template["Resources"]

    assert resources["ApplicationTable"]["Type"] == "AWS::DynamoDB::Table"
    assert resources["ApplicationTable"]["Properties"]["TimeToLiveSpecification"][
        "Enabled"
    ]
    indexes = {
        index["IndexName"]: index
        for index in resources["ApplicationTable"]["Properties"]["GlobalSecondaryIndexes"]
    }
    assert indexes["ScheduleIndex"]["KeySchema"][1]["AttributeName"] == "next_run_at"
    assert indexes["ScheduleIndex"]["Projection"]["NonKeyAttributes"] == [
        "user_id",
        "policy_hash",
        "consent_version",
        "connection_version",
    ]
    assert indexes["OwnerIndex"]["Projection"]["ProjectionType"] == "INCLUDE"
    assert indexes["OwnerIndex"]["Projection"]["NonKeyAttributes"] == [
        "connection_version"
    ]
    assert resources["TokenKey"]["Properties"]["EnableKeyRotation"] is True
    assert resources["TokenKey"]["DeletionPolicy"] == "Retain"
    assert resources["TokenKey"]["UpdateReplacePolicy"] == "Retain"
    assert resources["ApplicationTable"]["DeletionPolicy"] == "Retain"
    assert resources["ClassificationQueue"]["Properties"]["FifoQueue"] is True
    queue = resources["ClassificationQueue"]["Properties"]
    assert queue["VisibilityTimeout"] >= 1800
    assert queue["RedrivePolicy"]["maxReceiveCount"] >= 5
    assert "QueueName" not in queue
    assert "QueueName" not in resources["ClassificationDeadLetterQueue"]["Properties"]
    worker = resources["WorkerFunction"]["Properties"]
    assert any("SQSPollerPolicy" in policy for policy in worker["Policies"])
    assert "dynamodb:TransactWriteItems" in str(worker["Policies"])
    mapping = resources["WorkerQueueMapping"]["Properties"]
    assert mapping["BatchSize"] == 1
    assert mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert mapping["MetricsConfig"]["Metrics"] == ["EventCount"]
    assert resources["DispatcherFunction"]["Properties"]["Events"]["Schedule"][
        "Type"
    ] == "ScheduleV2"
    assert resources["DispatcherFunction"]["Properties"]["ReservedConcurrentExecutions"] == 1
    assert resources["WebFunction"]["Properties"]["Handler"].endswith(
        "web_handler.lambda_handler"
    )
    dispatcher_policies = str(resources["DispatcherFunction"]["Properties"]["Policies"])
    assert "dynamodb:DeleteItem" in dispatcher_policies
    assert "dynamodb:TransactWriteItems" in dispatcher_policies
    for alarm in (
        "DeadLetterAlarm",
        "WorkerErrorAlarm",
        "TerminalRunFailureAlarm",
        "DispatcherErrorAlarm",
        "QueueAgeAlarm",
    ):
        assert resources[alarm]["Properties"]["AlarmActions"]

    globals_env = template["Globals"]["Function"]["Environment"]["Variables"]
    for name in (
        "OPERATOR_NAME",
        "PRIVACY_CONTACT_EMAIL",
        "SUPPORT_EMAIL",
        "PRIVACY_EFFECTIVE_DATE",
    ):
        assert name in globals_env
    interval = int(globals_env["AUTOMATIC_INTERVAL_SECONDS"])
    automatic_budget = int(globals_env["AUTOMATIC_RUNS_PER_DAY"])
    assert automatic_budget >= (86_400 + interval - 1) // interval

    pattern = template["Parameters"]["CustomDomainName"]["AllowedPattern"]
    assert re.fullmatch(pattern, "mail.example.org")
    assert not re.fullmatch(pattern, "https://mail.example.org/base")
    web_api = resources["WebApi"]["Properties"]
    assert web_api["DisableExecuteApiEndpoint"] is True
    assert web_api["Domain"]["SecurityPolicy"] == "TLS_1_2"


def test_web_stack_uses_shared_secrets_and_kms_instead_of_per_user_secret_file() -> None:
    text = (ROOT / "template-web.yaml").read_text()

    assert "GOOGLE_OAUTH_CLIENT_SECRET_ID" in text
    assert "OPENAI_API_KEY_SECRET_ID" in text
    assert "TOKEN_KMS_KEY_ID" in text
    assert "GMAIL_TOKEN_FILE" not in text
    assert "gmail_oauth_secret.json" not in text
    assert "dynamodb:BatchWriteItem" not in text
    assert "dynamodb:TransactWriteItems" in text


def test_public_templates_do_not_contain_placeholder_legal_copy() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "email_classification_agent" / "templates").glob("*.html")
    ).casefold()

    assert "example.com" not in text
    assert "replace this draft" not in text
    assert "privacy@localhost" not in text
    assert "support@localhost" not in text
