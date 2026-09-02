from __future__ import annotations

import json
import time
from io import BytesIO
from zipfile import ZipFile

from email_classification_agent.daily_report import (
    build_daily_report_xlsx,
    daily_messages,
    local_date_key,
    qualified_acquisition_properties,
)
from email_classification_agent.universal_models import RunRecord, UserRecord


def _run_with_report(report: dict) -> RunRecord:
    now = int(time.time())
    return RunRecord(
        run_id="run-1",
        user_id="user-1",
        mode="automatic",
        status="completed",
        policy_hash="policy",
        created_at=now,
        updated_at=now,
        expires_at=now + 3600,
        report_json=json.dumps(report),
        connection_version="v1",
    )


def test_daily_messages_marks_property_target_as_important() -> None:
    report = {
        "scanned": 2,
        "outcomes": [
            {
                "message_id": "m1",
                "thread_id": "t1",
                "subject": "Dade County deal",
                "sender": "Deals <deals@example.com>",
                "proposed_label": "Acquisitions/Wholesale",
                "secondary_label": "Acquisitions/Wholesale/Miami-Dade/Important",
                "confidence": 0.99,
                "action": "label_and_move",
                "reason": "Meets client acquisition criteria.",
                "evidence": ["priced target property"],
                "property_records": [
                    {
                        "address": "16225 NE 2nd Ave",
                        "asking_price": 262500,
                        "folio": "30-2218-007-2720",
                        "municipality": "UNINCORPORATED COUNTY",
                        "land_use": "RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                        "lookup_status": "verified",
                        "is_folio_30": True,
                        "is_unincorporated": True,
                        "has_double_lot": True,
                        "qualifies": True,
                        "lot_size_sqft": 11000,
                        "reasons": ["folio starts with 30", "asking price is within target"],
                    }
                ],
            },
            {
                "message_id": "m2",
                "thread_id": "t2",
                "subject": "Florida business news",
                "sender": "News <news@example.com>",
                "proposed_label": "News",
                "confidence": 0.98,
                "action": "label_and_move",
                "reason": "News digest.",
                "evidence": [],
            },
        ],
    }
    run = _run_with_report(report)

    messages = daily_messages([run], report_date=local_date_key(run.created_at))

    assert len(messages) == 2
    assert messages[0].is_important is True
    assert "16225 NE 2nd Ave" in messages[0].why_important
    assert messages[1].is_important is False


def test_daily_report_xlsx_contains_reviewable_rows_without_email_body() -> None:
    report = {
        "scanned": 1,
        "outcomes": [
            {
                "message_id": "m1",
                "thread_id": "t1",
                "subject": "Important deal",
                "sender": "Deals <deals@example.com>",
                "proposed_label": "Acquisitions/Wholesale",
                "secondary_label": "Acquisitions/Wholesale/Miami-Dade/Important",
                "confidence": 0.99,
                "action": "label_and_move",
                "reason": "Important property match.",
                "evidence": ["folio 30", "double lot"],
                "body": "This full private email body must not be exported.",
                "property_records": [
                    {
                        "address": "16225 NE 2nd Ave",
                        "asking_price": 262500,
                        "folio": "30-2218-007-2720",
                        "municipality": "UNINCORPORATED COUNTY",
                        "lookup_status": "verified",
                        "is_folio_30": True,
                        "is_unincorporated": True,
                        "has_double_lot": True,
                        "qualifies": True,
                        "reasons": ["asking price is within target"],
                    }
                ],
            },
            {
                "message_id": "m2",
                "thread_id": "t2",
                "subject": "Non target acquisition deal",
                "sender": "Deals <deals@example.com>",
                "proposed_label": "Acquisitions/Wholesale",
                "secondary_label": "Acquisitions/Wholesale/Miami-Dade",
                "confidence": 0.99,
                "action": "label_and_move",
                "reason": "Acquisition email, but not an important property.",
                "evidence": ["property"],
                "property_records": [
                    {
                        "address": "3300 NW 50th St",
                        "asking_price": 419000,
                        "folio": "30-3121-019-0190",
                        "municipality": "UNINCORPORATED COUNTY",
                        "lookup_status": "verified",
                        "is_folio_30": True,
                        "is_unincorporated": True,
                        "has_double_lot": False,
                        "qualifies": False,
                        "reasons": ["asking price is above target"],
                    }
                ],
            },
            {
                "message_id": "m3",
                "thread_id": "t3",
                "subject": "Florida news",
                "sender": "News <news@example.com>",
                "proposed_label": "News",
                "confidence": 0.98,
                "action": "label_and_move",
                "reason": "News digest.",
                "evidence": [],
            }
        ],
    }
    run = _run_with_report(report)
    user = UserRecord(
        user_id="user-1",
        email="buyer@example.com",
        encrypted_grant="cipher",
        connection_version="v1",
    )

    workbook = build_daily_report_xlsx(
        user=user,
        runs=[run],
        report_date=local_date_key(run.created_at),
    )
    qualified_rows = qualified_acquisition_properties(
        [run],
        report_date=local_date_key(run.created_at),
    )

    with ZipFile(BytesIO(workbook)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "xl/workbook.xml" in archive.namelist()
        workbook_xml = archive.read("xl/workbook.xml").decode()
        properties_sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        summary_sheet = archive.read("xl/worksheets/sheet2.xml").decode()

    assert len(qualified_rows) == 1
    assert "Qualified Properties" in workbook_xml
    assert "Daily Summary" in workbook_xml
    assert "16225 NE 2nd Ave" in properties_sheet
    assert "asking price is within target" in properties_sheet
    assert "Important deal" in properties_sheet
    assert "This full private email body must not be exported" not in properties_sheet
    assert "3300 NW 50th St" not in properties_sheet
    assert "Florida news" not in properties_sheet
    assert "Qualified properties exported" in summary_sheet
