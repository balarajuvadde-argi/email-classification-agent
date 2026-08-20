import json
from pathlib import Path

from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.email_parser import parse_eml
from email_classification_agent.models import Category

FIXTURES = Path(__file__).parent / "fixtures"


def test_all_supplied_wholesale_samples_classify_correctly() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    pipeline = ClassificationPipeline(Settings(use_llm=False))
    assert len(manifest) == 9

    failures: list[str] = []
    for item in manifest:
        message = parse_eml(FIXTURES / item["fixture"])
        result = pipeline.classify(message)
        if result.category is not Category.WHOLESALE:
            failures.append(f"{item['original_name']}: {result.reason}")
        assert result.source == "deterministic_high_confidence"
        assert result.confidence >= 0.99

    assert not failures, "\n".join(failures)
