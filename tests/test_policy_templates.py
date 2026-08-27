from email_classification_agent.policy_templates import POLICY_TEMPLATES, get_policy_template


def test_policy_templates_cover_default_acquisition_workflows() -> None:
    template_ids = {template.template_id for template in POLICY_TEMPLATES}

    assert template_ids == {
        "all_acquisitions_news",
        "on_market_only",
        "off_market_only",
        "wholesale_only",
        "news_only",
    }
    assert get_policy_template("all_acquisitions_news").labels == (
        "Acquisitions/On Market",
        "Acquisitions/Off Market",
        "Acquisitions/Wholesale",
        "News",
    )


def test_unknown_policy_template_is_not_accepted() -> None:
    assert get_policy_template("missing") is None
