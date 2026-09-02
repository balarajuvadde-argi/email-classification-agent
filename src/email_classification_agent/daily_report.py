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


@dataclass(frozen=True, slots=True)
class QualifiedAcquisitionProperty:
    run_id: str
    run_mode: str
    run_time_label: str
    message_id: str
    subject: str
    sender: str
    primary_label: str
    secondary_label: str
    confidence: float
    why_important: str
    address: str
    asking_price: float | None
    folio: str
    municipality: str
    land_use: str
    lookup_status: str
    is_folio_30: bool
    is_unincorporated: bool
    has_double_lot: bool
    lot_size_sqft: float | None
    legal_description: str
    reasons: str


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


def _label_is_acquisition(label: str) -> bool:
    folded = label.strip().casefold()
    return (
        folded == "acquisitions"
        or folded.startswith("acquisitions/")
        or folded in {"wholesale", "wholesaler", "on market", "off market"}
        or folded.endswith("/wholesale")
        or folded.endswith("/on market")
        or folded.endswith("/off market")
    )


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


def qualified_acquisition_properties(
    runs: list[RunRecord],
    *,
    report_date: str,
    timezone_name: str = "America/New_York",
) -> list[QualifiedAcquisitionProperty]:
    rows: list[QualifiedAcquisitionProperty] = []
    for message in daily_messages(
        runs,
        report_date=report_date,
        timezone_name=timezone_name,
    ):
        if not _label_is_acquisition(message.proposed_label):
            continue
        qualifying_records = [
            record
            for record in message.property_records
            if isinstance(record, dict) and bool(record.get("qualifies"))
        ]
        for record in qualifying_records:
            reasons = [
                str(reason)
                for reason in record.get("reasons") or []
                if str(reason).strip()
            ]
            why = "; ".join(reasons) or message.why_important or message.reason
            rows.append(
                QualifiedAcquisitionProperty(
                    run_id=message.run_id,
                    run_mode=message.run_mode,
                    run_time_label=message.run_time_label,
                    message_id=message.message_id,
                    subject=message.subject,
                    sender=message.sender,
                    primary_label=message.proposed_label,
                    secondary_label=message.secondary_label,
                    confidence=message.confidence,
                    why_important=why,
                    address=str(record.get("address") or ""),
                    asking_price=_optional_float(record.get("asking_price")),
                    folio=str(record.get("folio") or ""),
                    municipality=str(record.get("municipality") or ""),
                    land_use=str(record.get("land_use") or ""),
                    lookup_status=str(record.get("lookup_status") or ""),
                    is_folio_30=bool(record.get("is_folio_30")),
                    is_unincorporated=bool(record.get("is_unincorporated")),
                    has_double_lot=bool(record.get("has_double_lot")),
                    lot_size_sqft=_optional_float(record.get("lot_size_sqft")),
                    legal_description=str(record.get("legal_description") or ""),
                    reasons="; ".join(reasons),
                )
            )
    return rows


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    acquisition_messages = [
        message for message in messages if _label_is_acquisition(message.proposed_label)
    ]
    qualified_properties = qualified_acquisition_properties(
        runs,
        report_date=report_date,
        timezone_name=timezone_name,
    )
    source_message_ids = {property_row.message_id for property_row in qualified_properties}
    label_counts = Counter(property_row.primary_label for property_row in qualified_properties)
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
        ["Acquisition emails identified", len(acquisition_messages)],
        ["Qualified properties exported", len(qualified_properties)],
        ["Source emails with qualified properties", len(source_message_ids)],
        ["Generated at", local_datetime_label(int(datetime.now(UTC).timestamp()), timezone_name)],
        [],
        ["Acquisition label", "Qualified property count"],
    ]
    summary_rows.extend([label, count] for label, count in sorted(label_counts.items()))

    property_rows: list[list[Any]] = [
        [
            "Run time",
            "Acquisition type",
            "Property address",
            "Asking price",
            "Why important",
            "Folio",
            "Municipality",
            "Land use",
            "Lookup status",
            "Folio 30",
            "Unincorporated",
            "Double lot",
            "Lot size sqft",
            "Legal description",
            "Email subject",
            "Sender",
            "Primary label",
            "Secondary label",
            "Confidence",
            "Gmail message ID",
        ]
    ]
    if qualified_properties:
        property_rows.extend(
            [
                property_row.run_time_label,
                property_row.primary_label.rsplit("/", 1)[-1],
                property_row.address,
                property_row.asking_price,
                property_row.why_important,
                property_row.folio,
                property_row.municipality,
                property_row.land_use,
                property_row.lookup_status,
                property_row.is_folio_30,
                property_row.is_unincorporated,
                property_row.has_double_lot,
                property_row.lot_size_sqft,
                property_row.legal_description,
                property_row.subject,
                property_row.sender,
                property_row.primary_label,
                property_row.secondary_label,
                property_row.confidence,
                property_row.message_id,
            ]
            for property_row in qualified_properties
        )
    else:
        property_rows.append(
            [
                "",
                "",
                "No qualified acquisition properties found for this report date.",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )

    sheet_names = ["Qualified Properties", "Daily Summary"]
    sheets = [
        _sheet_xml(property_rows, widths=[24, 18, 32, 14, 85, 20, 22, 34, 18, 12, 16, 12, 14, 60, 42, 34, 24, 34, 12, 24]),
        _sheet_xml(summary_rows, widths=[34, 80]),
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
