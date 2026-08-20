from email_classification_agent.gmail_client import GmailClient


class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Messages:
    def __init__(self):
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _Request({"messages": [{"id": "m1"}, {"id": "m2"}]})


class _Labels:
    def __init__(self):
        self.create_calls = []
        self.counter = 0

    def list(self, **kwargs):
        return _Request({"labels": []})

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        self.counter += 1
        return _Request({"id": f"L{self.counter}"})


class _Users:
    def __init__(self):
        self.messages_api = _Messages()
        self.labels_api = _Labels()

    def messages(self):
        return self.messages_api

    def labels(self):
        return self.labels_api


class _Service:
    def __init__(self):
        self.users_api = _Users()

    def users(self):
        return self.users_api


def test_candidate_query_is_limited_to_inbox_and_excludes_spam_trash() -> None:
    service = _Service()
    client = GmailClient(service)
    ids = client.list_candidate_message_ids(
        base_query="in:inbox -in:spam -in:trash",
        processed_label_name="EmailAgent/Processed/v2",
        max_results=10,
    )

    assert ids == ["m1", "m2"]
    call = service.users_api.messages_api.list_calls[0]
    assert call["labelIds"] == ["INBOX"]
    assert call["includeSpamTrash"] is False
    assert '-label:"EmailAgent/Processed/v2"' in call["q"]


def test_label_visibility_matches_visible_and_hidden_roles() -> None:
    service = _Service()
    client = GmailClient(service)

    wholesale_id = client.ensure_label("Wholesale", visible=True, create=True)
    processed_id = client.ensure_label("EmailAgent/Processed/v2", visible=False, create=True)

    assert wholesale_id == "L1"
    assert processed_id == "L2"
    assert service.users_api.labels_api.create_calls == [
        {
            "userId": "me",
            "body": {
                "name": "Wholesale",
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
            },
        },
        {
            "userId": "me",
            "body": {
                "name": "EmailAgent/Processed/v2",
                "messageListVisibility": "hide",
                "labelListVisibility": "labelHide",
            },
        },
    ]
