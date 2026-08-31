from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from .universal_models import RunRecord, UserRecord


@dataclass(frozen=True, slots=True)
class DailyReportMessage:
    run_id: str
    run_mode: str
    run_status: str
    run_created_at: int
    run_time_label: str
    message_id: str
    thread_id: str
    subject: str
    sender: str
    proposed_label: str
    secondary_label: str
    confidence: float
    action: str
    reason: str
    evidence: str
    property_records: tuple[dict[str, Any], ...]
    is_important: bool
    why_important: str


def local_date_key(timestamp: int, timezone_name: str = "America/New_York") -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).astimezone(
        ZoneInfo(timezone_name)
    ).date().isoformat()


def local_datetime_label(timestamp: int, timezone_name: str = "America/New_York") -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).astimezone(
        ZoneInfo(timezone_name)
    ).strftime("%Y-%m-%d %I:%M %p %Z")


def _report_from_run(run: RunRecord) -> dict[str, Any]:
    if not run.report_json:
        return {}
    try:
        value = json.loads(run.report_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _label_is_important(label: str) -> bool:
    folded = label.casefold()
    return folded == "important" or folded.endswith("/important")


def _outcome_is_important(outcome: dict[str, Any]) -> bool:
    labels = [
        str(outcome.get("proposed_label") or ""),
        str(outcome.get("secondary_label") or ""),
    ]
    if any(_label_is_important(label) for label in labels if label):
        return True
    return any(
        bool(record.get("qualifies"))
        for record in outcome.get("property_records") or []
        if isinstance(record, dict)
    )


def _why_important(outcome: dict[str, Any]) -> str:
    qualifying_records = [
        record
        for record in outcome.get("property_records") or []
        if isinstance(record, dict) and bool(record.get("qualifies"))
    ]
    if not qualifying_records:
        return str(outcome.get("reason") or "")
    parts = []
    for record in qualifying_records:
        address = str(record.get("address") or "property")
        reasons = [
            str(reason)
            for reason in record.get("reasons") or []
            if str(reason).strip()
        ]
        detail = "; ".join(reasons) if reasons else "matched the Important property rules"
        parts.append(f"{address}: {detail}")
    return "Important because " + " | ".join(parts)


def daily_messages(
    runs: list[RunRecord],
    *,
    report_date: str,
    timezone_name: str = "America/New_York",
) -> list[DailyReportMessage]:
    rows: list[DailyReportMessage] = []
    for run in runs:
        if local_date_key(run.created_at, timezone_name) != report_date:
            continue
        report = _report_from_run(run)
        for outcome in report.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            records = tuple(
                record
                for record in outcome.get("property_records") or []
                if isinstance(record, dict)
            )
            important = _outcome_is_important(outcome)
            rows.append(
                DailyReportMessage(
                    run_id=run.run_id,
                    run_mode=run.mode,
                    run_status=run.status,
                    run_created_at=run.created_at,
                    run_time_label=local_datetime_label(run.created_at, timezone_name),
                    message_id=str(outcome.get("message_id") or ""),
                    thread_id=str(outcome.get("thread_id") or ""),
                    subject=str(outcome.get("subject") or ""),
                    sender=str(outcome.get("sender") or ""),
                    proposed_label=str(outcome.get("proposed_label") or ""),
                    secondary_label=str(outcome.get("secondary_label") or ""),
                    confidence=float(outcome.get("confidence") or 0.0),
                    action=str(outcome.get("action") or ""),
                    reason=str(outcome.get("reason") or ""),
                    evidence="; ".join(str(item) for item in outcome.get("evidence") or []),
                    property_records=records,
                    is_important=important,
                    why_important=_why_important(outcome) if important else "",
                )
            )
    return rows


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell_xml(row: int, column: int, value: Any, *, style: int = 0) -> str:
    reference = f"{_column_name(column)}{row}"
    style_attr = f' s="{style}"' if style else ""
    if value is None:
        return f'<c r="{reference}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value), quote=False)
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def _sheet_xml(rows: list[list[Any]], *, widths: list[int] | None = None) -> str:
    columns = ""
    if widths:
        columns = "<cols>" + "".join(
            f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
            for idx, width in enumerate(widths, start=1)
        ) + "</cols>"
    row_xml = []
    for row_idx, values in enumerate(rows, start=1):
        style = 1 if row_idx == 1 else 0
        cells = "".join(
            _cell_xml(row_idx, column_idx, value, style=style)
            for column_idx, value in enumerate(values, start=1)
        )
        row_xml.append(f'<row r="{row_idx}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{columns}<sheetData>{''.join(row_xml)}</sheetData></worksheet>"
    )


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )


def _content_types_xml(sheet_count: int) -> str:
    worksheets = "".join(
        (
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{worksheets}</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    relationships = "".join(
        (
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
        for idx in range(1, sheet_count + 1)
    )
    relationships += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{relationships}</Relationships>"
    )


def build_daily_report_xlsx(
    *,
    user: UserRecord,
    runs: list[RunRecord],
    report_date: str,
    timezone_name: str = "America/New_York",
) -> bytes:
    messages = daily_messages(runs, report_date=report_date, timezone_name=timezone_name)
    important = [message for message in messages if message.is_important]
    label_counts = Counter(
        message.secondary_label or message.proposed_label or "No label"
        for message in messages
    )
    total_scanned = 0
    for run in runs:
        if local_date_key(run.created_at, timezone_name) == report_date:
            total_scanned += int(_report_from_run(run).get("scanned") or 0)

    summary_rows: list[list[Any]] = [
        ["Metric", "Value"],
        ["Report date", report_date],
        ["Timezone", timezone_name],
        ["User email", user.email],
        ["Total emails scanned", total_scanned],
        ["Processed message rows", len(messages)],
        ["Important emails", len(important)],
        ["Generated at", local_datetime_label(int(datetime.now(UTC).timestamp()), timezone_name)],
        [],
        ["Label", "Count"],
    ]
    summary_rows.extend([label, count] for label, count in sorted(label_counts.items()))

    important_rows: list[list[Any]] = [
        [
            "Run time",
            "Subject",
            "Sender",
            "Primary label",
            "Secondary label",
            "Confidence",
            "Why important",
            "Evidence",
            "Gmail message ID",
        ]
    ]
    important_rows.extend(
        [
            message.run_time_label,
            message.subject,
            message.sender,
            message.proposed_label,
            message.secondary_label,
            message.confidence,
            message.why_important,
            message.evidence,
            message.message_id,
        ]
        for message in important
    )

    processed_rows: list[list[Any]] = [
        [
            "Run time",
            "Run mode",
            "Subject",
            "Sender",
            "Primary label",
            "Secondary label",
            "Confidence",
            "Action",
            "Reason",
            "Evidence",
            "Important",
            "Gmail message ID",
        ]
    ]
    processed_rows.extend(
        [
            message.run_time_label,
            message.run_mode,
            message.subject,
            message.sender,
            message.proposed_label,
            message.secondary_label,
            message.confidence,
            message.action,
            message.reason,
            message.evidence,
            "Yes" if message.is_important else "No",
            message.message_id,
        ]
        for message in messages
    )

    property_rows: list[list[Any]] = [
        [
            "Run time",
            "Email subject",
            "Address",
            "Asking price",
            "Folio",
            "Municipality",
            "Land use",
            "Lookup status",
            "Folio 30",
            "Unincorporated",
            "Double lot",
            "Target match",
            "Lot size sqft",
            "Reasons",
        ]
    ]
    for message in messages:
        for record in message.property_records:
            property_rows.append(
                [
                    message.run_time_label,
                    message.subject,
                    record.get("address") or "",
                    record.get("asking_price"),
                    record.get("folio") or "",
                    record.get("municipality") or "",
                    record.get("land_use") or "",
                    record.get("lookup_status") or "",
                    bool(record.get("is_folio_30")),
                    bool(record.get("is_unincorporated")),
                    bool(record.get("has_double_lot")),
                    bool(record.get("qualifies")),
                    record.get("lot_size_sqft"),
                    "; ".join(str(item) for item in record.get("reasons") or []),
                ]
            )

    sheet_names = ["Daily Summary", "Important Emails", "All Processed", "Properties"]
    sheets = [
        _sheet_xml(summary_rows, widths=[28, 80]),
        _sheet_xml(important_rows, widths=[24, 42, 34, 24, 34, 12, 80, 55, 24]),
        _sheet_xml(processed_rows, widths=[24, 18, 42, 34, 24, 34, 12, 28, 65, 55, 12, 24]),
        _sheet_xml(property_rows, widths=[24, 42, 30, 14, 20, 22, 32, 18, 12, 16, 12, 14, 14, 75]),
    ]

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _content_types_xml(len(sheets)))
        workbook.writestr("_rels/.rels", _root_rels_xml())
        workbook.writestr("xl/workbook.xml", _workbook_xml(sheet_names))
        workbook.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheets)))
        workbook.writestr("xl/styles.xml", _styles_xml())
        for idx, sheet in enumerate(sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{idx}.xml", sheet)
    return output.getvalue()
