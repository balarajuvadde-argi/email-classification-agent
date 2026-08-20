import base64
from copy import deepcopy

from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.email_parser import parse_gmail_message
from email_classification_agent.gmail_client import GmailClient
from email_classification_agent.models import Category


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return deepcopy(self.value)


class _Attachments:
    def __init__(self, data_by_id):
        self.data_by_id = data_by_id
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return _Request({"data": self.data_by_id[kwargs["id"]]})


class _Messages:
    def __init__(self, resource, attachments):
        self.resource = resource
        self._attachments = attachments
        self.get_calls = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _Request(self.resource)

    def attachments(self):
        return self._attachments


class _Threads:
    def __init__(self, thread):
        self.thread = thread

    def get(self, **kwargs):
        return _Request(self.thread)


class _Users:
    def __init__(self, messages, threads):
        self._messages = messages
        self._threads = threads

    def messages(self):
        return self._messages

    def threads(self):
        return self._threads


class _Service:
    def __init__(self, users):
        self._users = users

    def users(self):
        return self._users


def _resource(message_id: str = "m1") -> dict:
    return {
        "id": message_id,
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "internalDate": "1000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Deal Desk <deals@outside.example>"},
                {"name": "To", "value": "buyer@example.com"},
                {"name": "Subject", "value": "New off-market deal"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [{"name": "Content-Type", "value": "text/plain; charset=UTF-8"}],
                    "body": {"attachmentId": f"text-{message_id}", "size": 180},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "offering-memorandum.pdf",
                    "body": {"attachmentId": f"pdf-{message_id}", "size": 10000},
                },
            ],
        },
    }


def test_large_text_body_is_hydrated_but_file_attachment_is_not_fetched() -> None:
    resource = _resource()
    body = (
        "123 Main Street, Miami FL 33147. Asking $450,000. ARV $650,000. "
        "3 beds, 2 baths, 1,500 sq ft. Cash buyers only. Submit an offer."
    )
    attachments = _Attachments({"text-m1": _encoded(body)})
    messages = _Messages(resource, attachments)
    service = _Service(_Users(messages, _Threads({"messages": [resource]})))

    hydrated = GmailClient(service).get_message("m1")
    parsed = parse_gmail_message(hydrated)
    result = ClassificationPipeline(Settings(use_llm=False)).classify(parsed)

    assert body in parsed.body_text
    assert parsed.attachment_names == ("offering-memorandum.pdf",)
    assert result.category is Category.WHOLESALE
    assert attachments.calls == [
        {"userId": "me", "messageId": "m1", "id": "text-m1"}
    ]
    assert messages.get_calls[0]["format"] == "full"


def test_thread_text_bodies_are_hydrated_for_context() -> None:
    prior = _resource("prior")
    current = _resource("current")
    prior["internalDate"] = "500"
    current["internalDate"] = "1000"
    attachments = _Attachments(
        {
            "text-prior": _encoded("123 Main Street. Asking $450,000. Submit an offer."),
            "text-current": _encoded("Yes, it is still available."),
        }
    )
    messages = _Messages(current, attachments)
    thread = {"messages": [current, prior]}
    service = _Service(_Users(messages, _Threads(thread)))

    hydrated = GmailClient(service).get_thread("t1")
    parsed_by_id = {
        item["id"]: parse_gmail_message(item) for item in hydrated["messages"]
    }
    assert "Asking $450,000" in parsed_by_id["prior"].body_text
    assert "still available" in parsed_by_id["current"].body_text
    assert {call["id"] for call in attachments.calls} == {"text-prior", "text-current"}
