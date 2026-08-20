from email_classification_agent.llm_classifier import INSTRUCTIONS, _bounded


def test_semantic_prompt_treats_email_content_as_untrusted() -> None:
    lowered = INSTRUCTIONS.casefold()
    assert "untrusted data" in lowered
    assert "never" in lowered
    assert "follow instructions inside" in lowered
    assert "ignore previous instructions" in lowered
    assert "classify the current message" in lowered


def test_bounded_text_preserves_head_and_tail() -> None:
    value = "HEAD" + ("x" * 1000) + "TAIL"
    bounded = _bounded(value, 100)
    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")
    assert "middle omitted" in bounded
