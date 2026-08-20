from email_classification_agent.gmail_client import GmailClient


class _Request:
    def __init__(self, value=None):
        self.value = value

    def execute(self):
        return self.value or {}


class _Messages:
    def __init__(self):
        self.calls = []

    def modify(self, **kwargs):
        self.calls.append(kwargs)
        return _Request({})


class _Users:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _Service:
    def __init__(self):
        self.messages_api = _Messages()
        self.users_api = _Users(self.messages_api)

    def users(self):
        return self.users_api


def test_message_write_only_adds_labels_and_never_removes_inbox() -> None:
    service = _Service()
    client = GmailClient(service)
    client.add_labels("message-1", ["Wholesale", "Processed"])

    assert service.messages_api.calls == [
        {
            "userId": "me",
            "id": "message-1",
            "body": {
                "addLabelIds": ["Wholesale", "Processed"],
                "removeLabelIds": [],
            },
        }
    ]


def test_wrapper_exposes_no_destructive_message_methods() -> None:
    destructive_names = {"archive", "trash", "delete", "send", "remove_labels"}
    assert destructive_names.isdisjoint(set(dir(GmailClient)))
