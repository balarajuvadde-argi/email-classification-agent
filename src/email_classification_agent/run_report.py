from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from .universal_models import RunRecord, UserRecord

REPORT_TIMEZONE = "America/New_York"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class ImportantPropertyRow:
    run_id: str
    run_time: str
    mailbox: str
    subject: str
    sender: str
    label: str
    secondary_label: str
    confidence: float
    address: str
    asking_price: float | None
    folio: str
    municipality: str
    land_use: str
    lot_size_sqft: float | None
    reasons: str
    classification_reason: str


def important_acquisition_properties(
    run: RunRecord,
    *,
    timezone_name: str = REPORT_TIMEZONE,
) -> list[ImportantPropertyRow]:
    report = _run_report(run)
    if not report:
        return []
    mailbox = str(report.get("mailbox") or "")
    run_time = _format_epoch(run.updated_at or run.created_at, timezone_name)
    rows: list[ImportantPropertyRow] = []
    for outcome in report.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        label = str(outcome.get("proposed_label") or "")
        if not _is_acquisition_label(label):
            continue
        for record in outcome.get("property_records") or []:
            if not isinstance(record, dict) or record.get("qualifies") is not True:
                continue
            reasons = record.get("reasons") or []
            rows.append(
                ImportantPropertyRow(
                    run_id=run.run_id,
                    run_time=run_time,
                    mailbox=mailbox,
                    subject=str(outcome.get("subject") or ""),
                    sender=str(outcome.get("sender") or ""),
                    label=label,
                    secondary_label=str(outcome.get("secondary_label") or ""),
                    confidence=float(outcome.get("confidence") or 0.0),
                    address=str(record.get("address") or ""),
                    asking_price=_number(record.get("asking_price")),
                    folio=str(record.get("folio") or ""),
                    municipality=str(record.get("municipality") or ""),
                    land_use=str(record.get("land_use") or ""),
                    lot_size_sqft=_number(record.get("lot_size_sqft")),
                    reasons="; ".join(str(reason) for reason in reasons if reason),
                    classification_reason=str(outcome.get("reason") or ""),
                )
            )
    return rows


def build_important_properties_xlsx(
    user: UserRecord,
    run: RunRecord,
    *,
    timezone_name: str = REPORT_TIMEZONE,
) -> bytes:
    report = _run_report(run)
    rows = important_acquisition_properties(run, timezone_name=timezone_name)
    generated_at = _format_epoch(int(datetime.now(tz=ZoneInfo(timezone_name)).timestamp()), timezone_name)
    workbook_rows: list[list[Any]] = [
        [
            "Run ID",
            "Run Time",
            "Mailbox",
            "Source Email Subject",
            "Sender",
            "Gmail Label",
            "Secondary Label",
            "Confidence",
            "Property Address",
            "Asking Price",
            "Folio",
            "Municipality",
            "Land Use",
            "Lot Size Sq Ft",
            "Why Important",
            "Classification Reason",
        ],
        *[
            [
                row.run_id,
                row.run_time,
                row.mailbox,
                row.subject,
                row.sender,
                row.label,
                row.secondary_label,
                row.confidence,
                row.address,
                row.asking_price,
                row.folio,
                row.municipality,
                row.land_use,
                row.lot_size_sqft,
                row.reasons,
                row.classification_reason,
            ]
            for row in rows
        ],
    ]
    summary_rows = [
        ["Generated At", generated_at],
        ["Recipient Mailbox", user.email],
        ["Run ID", run.run_id],
        ["Run Mode", run.mode],
        ["Run Status", run.status],
        ["Run Completed At", _format_epoch(run.updated_at or run.created_at, timezone_name)],
        ["Messages Scanned", report.get("scanned", 0)],
        ["Labels Proposed", report.get("proposed", 0)],
        ["Labels Applied", report.get("labeled", 0)],
        ["Qualified Important Properties", len(rows)],
    ]
    return _make_xlsx(
        {
            "Important Properties": workbook_rows,
            "Run Summary": summary_rows,
        }
    )


def important_properties_filename(run: RunRecord, *, timezone_name: str = REPORT_TIMEZONE) -> str:
    day = _format_epoch(run.updated_at or run.created_at, timezone_name, date_only=True)
    short_id = re.sub(r"[^a-zA-Z0-9-]", "", run.run_id)[-8:] or "run"
    return f"important-acquisition-properties-{day}-{short_id}.xlsx"


def _run_report(run: RunRecord) -> dict[str, Any]:
    if not run.report_json:
        return {}
    try:
        value = json.loads(run.report_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _is_acquisition_label(label: str) -> bool:
    folded = label.casefold()
    leaf = folded.rsplit("/", 1)[-1]
    return folded.startswith("acquisitions/") or leaf in {
        "wholesale",
        "wholesaler",
        "on market",
        "on-market",
        "off market",
        "off-market",
    }


def _format_epoch(epoch_seconds: int, timezone_name: str, *, date_only: bool = False) -> str:
    timestamp = datetime.fromtimestamp(epoch_seconds, ZoneInfo(timezone_name))
    return timestamp.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %I:%M %p %Z")


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _make_xlsx(sheets: dict[str, list[list[Any]]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _root_relationships())
        archive.writestr("xl/workbook.xml", _workbook_xml(list(sheets)))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships(len(sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, rows in enumerate(sheets.values(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))
    return output.getvalue()


def _content_types(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
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
        f"{overrides}</Types>"
    )


def _root_relationships() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _workbook_relationships(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{relationships}"
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def _worksheet_xml(rows: list[list[Any]]) -> str:
    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_cell_xml(row_index, column_index, value) for column_index, value in enumerate(row, start=1))
        xml_rows.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        '</worksheet>'
    )


def _cell_xml(row_index: int, column_index: int, value: Any) -> str:
    ref = f"{_column_letter(column_index)}{row_index}"
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value:g}</v></c>'
    text = escape(str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
