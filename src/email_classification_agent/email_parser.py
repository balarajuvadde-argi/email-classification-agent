from __future__ import annotations

import base64
import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .models import ParsedEmail


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
    }
    IGNORED_TAGS = {"script", "style", "head", "title", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "img":
            alt = next((value for key, value in attrs if key.lower() == "alt"), None)
            if alt:
                self.parts.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return normalize_text(unescape(parser.text()))


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _decode_bytes(data: bytes, charset: str | None) -> str:
    for encoding in (charset, "utf-8", "latin-1"):
        if not encoding:
            continue
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _message_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""
        if disposition == "attachment" or filename:
            continue
        content_type = part.get_content_type().lower()
        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            text = raw if isinstance(raw, str) else ""
        else:
            text = _decode_bytes(payload, part.get_content_charset())
        if content_type == "text/plain":
            plain_parts.append(text)
        elif content_type == "text/html":
            html_parts.append(text)

    if plain_parts:
        return normalize_text("\n\n".join(plain_parts))
    if html_parts:
        return html_to_text("\n\n".join(html_parts))
    return ""


def _message_attachment_names(message: Message) -> tuple[str, ...]:
    names: list[str] = []
    for part in message.walk():
        filename = part.get_filename()
        if filename:
            names.append(str(filename))
    return tuple(dict.fromkeys(names))


def _parsed_from_email_message(
    message: EmailMessage,
    *,
    message_id: str,
    thread_id: str,
    label_ids: tuple[str, ...],
    internal_date_ms: int,
) -> ParsedEmail:
    headers = {key.lower(): str(value) for key, value in message.items()}
    return ParsedEmail(
        message_id=message_id,
        thread_id=thread_id,
        label_ids=label_ids,
        internal_date_ms=internal_date_ms,
        from_header=headers.get("from", ""),
        sender_header=headers.get("sender", ""),
        reply_to_header=headers.get("reply-to", ""),
        to_header=headers.get("to", ""),
        subject=headers.get("subject", ""),
        body_text=_message_body(message),
        raw_headers=headers,
        attachment_names=_message_attachment_names(message),
    )


def parse_eml(path: Path, message_id: str | None = None) -> ParsedEmail:
    with path.open("rb") as handle:
        message: EmailMessage = BytesParser(policy=policy.default).parse(handle)
    headers = {key.lower(): str(value) for key, value in message.items()}
    resolved_id = message_id or headers.get("message-id", path.name)
    return _parsed_from_email_message(
        message,
        message_id=resolved_id,
        thread_id=resolved_id,
        label_ids=(),
        internal_date_ms=0,
    )


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _payload_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield payload
    for child in payload.get("parts") or []:
        yield from _payload_parts(child)


def _gmail_body(payload: dict[str, Any]) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _payload_parts(payload):
        mime_type = (part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        if filename:
            continue
        data = ((part.get("body") or {}).get("data") or "").strip()
        if not data:
            continue
        raw = _b64url_decode(data)
        headers = {
            (item.get("name") or "").lower(): item.get("value") or ""
            for item in part.get("headers") or []
        }
        charset_match = re.search(r"charset=[\"']?([^;\"']+)", headers.get("content-type", ""), re.I)
        charset = charset_match.group(1).strip() if charset_match else None
        text = _decode_bytes(raw, charset)
        if mime_type == "text/plain":
            plain_parts.append(text)
        elif mime_type == "text/html":
            html_parts.append(text)
    if plain_parts:
        return normalize_text("\n\n".join(plain_parts))
    if html_parts:
        return html_to_text("\n\n".join(html_parts))
    return ""


def _gmail_attachment_names(payload: dict[str, Any]) -> tuple[str, ...]:
    names = [
        str(part.get("filename"))
        for part in _payload_parts(payload)
        if part.get("filename")
    ]
    return tuple(dict.fromkeys(names))


def parse_gmail_message(resource: dict[str, Any]) -> ParsedEmail:
    message_id = str(resource.get("id") or "")
    thread_id = str(resource.get("threadId") or "")
    label_ids = tuple(str(value) for value in (resource.get("labelIds") or []))
    internal_date_ms = int(resource.get("internalDate") or 0)

    raw_value = str(resource.get("raw") or "").strip()
    if raw_value:
        raw_bytes = _b64url_decode(raw_value)
        message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        return _parsed_from_email_message(
            message,
            message_id=message_id,
            thread_id=thread_id,
            label_ids=label_ids,
            internal_date_ms=internal_date_ms,
        )

    payload = resource.get("payload") or {}
    headers = {
        (item.get("name") or "").lower(): item.get("value") or ""
        for item in payload.get("headers") or []
    }
    body_text = _gmail_body(payload)
    if not body_text:
        body_text = normalize_text(str(resource.get("snippet") or ""))
    return ParsedEmail(
        message_id=message_id,
        thread_id=thread_id,
        label_ids=label_ids,
        internal_date_ms=internal_date_ms,
        from_header=headers.get("from", ""),
        sender_header=headers.get("sender", ""),
        reply_to_header=headers.get("reply-to", ""),
        to_header=headers.get("to", ""),
        subject=headers.get("subject", ""),
        body_text=body_text,
        raw_headers=headers,
        attachment_names=_gmail_attachment_names(payload),
    )
