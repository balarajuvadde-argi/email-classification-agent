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


def test_sam_template_has_safe_scheduled_function_configuration() -> None:
    template = yaml.load((ROOT / "template.yaml").read_text(), Loader=_CfnLoader)
    function = template["Resources"]["EmailClassifierFunction"]
    properties = function["Properties"]

    assert function["Type"] == "AWS::Serverless::Function"
    assert properties["CodeUri"] == "src/"
    assert properties["Handler"] == "email_classification_agent.handler.lambda_handler"
    assert properties["Runtime"] == "python3.13"
    assert properties["ReservedConcurrentExecutions"] == 1
    variables = properties["Environment"]["Variables"]
    assert variables["DRY_RUN"] == "DryRun"
    assert variables["PROCESSED_LABEL"] == "EmailAgent/Processed/v2"
    assert variables["MIN_LLM_WHOLESALE_CONFIDENCE"] == "0.85"
    assert variables["MARK_LOW_CONFIDENCE_PROCESSED"] == "false"

    schedule = properties["Events"]["InboxSchedule"]
    assert schedule["Type"] == "ScheduleV2"
    assert schedule["Properties"]["State"] == "ENABLED"
    assert schedule["Properties"]["FlexibleTimeWindow"]["Mode"] == "OFF"


def test_lambda_requirements_are_present_inside_code_uri() -> None:
    root_requirements = (ROOT / "requirements.txt").read_text()
    lambda_requirements = (ROOT / "src" / "requirements.txt").read_text()
    assert root_requirements == lambda_requirements
    assert "google-api-python-client" in lambda_requirements
    assert "openai" in lambda_requirements
