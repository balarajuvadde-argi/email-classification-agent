import json
import zipfile
from io import BytesIO

from email_classification_agent.run_report import (
    build_important_properties_xlsx,
    important_acquisition_properties,
)
from email_classification_agent.universal_models import RunRecord, UserRecord


def _run() -> RunRecord:
    report = {
        "mailbox": "maurice@sargigroup.com",
        "dry_run": False,
        "scanned": 2,
        "proposed": 0,
        "labeled": 1,
        "outcomes": [
            {
                "subject": "Investor Alert",
                "sender": "Deals <deals@example.com>",
                "proposed_label": "Acquisitions/Wholesale",
                "secondary_label": "Acquisitions/Wholesale/Miami-Dade/Important",
                "confidence": 0.98,
                "reason": "Multiple off-market properties for sale.",
                "property_records": [
                    {
                        "address": "123 NW 1st St",
                        "asking_price": 250000,
                        "folio": "30-1234-001-0020",
                        "municipality": "UNINCORPORATED COUNTY",
                        "land_use": "RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                        "lot_size_sqft": 10000,
                        "qualifies": True,
                        "reasons": [
                            "folio starts with 30 (unincorporated Miami-Dade)",
                            "legal description indicates double/multiple lots",
                            "asking price $250,000 is at or below $275,000 target",
                        ],
                    },
                    {
                        "address": "999 NE 9th St",
                        "asking_price": 650000,
                        "qualifies": False,
                    },
                ],
            },
            {
                "subject": "Market news",
                "sender": "News <news@example.com>",
                "proposed_label": "News",
                "confidence": 0.95,
                "reason": "News",
                "property_records": [],
            },
        ],
    }
    return RunRecord(
        run_id="1234567890-abcdef",
        user_id="u1",
        mode="automatic",
        status="completed",
        policy_hash="hash",
        created_at=1_788_284_800,
        updated_at=1_788_285_000,
        expires_at=1_788_371_200,
        report_json=json.dumps(report),
        connection_version="v1",
    )


def test_important_acquisition_properties_keeps_only_qualified_acquisition_rows() -> None:
    rows = important_acquisition_properties(_run())

    assert len(rows) == 1
    assert rows[0].address == "123 NW 1st St"
    assert rows[0].asking_price == 250000
    assert "folio starts with 30" in rows[0].reasons


def test_build_important_properties_xlsx_creates_valid_workbook() -> None:
    user = UserRecord(user_id="u1", email="maurice@sargigroup.com", encrypted_grant="x")
    workbook = build_important_properties_xlsx(user, _run())

    with zipfile.ZipFile(BytesIO(workbook)) as archive:
        names = set(archive.namelist())
        workbook_xml = archive.read("xl/workbook.xml").decode()
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()

    assert "[Content_Types].xml" in names
    assert "Important Properties" in workbook_xml
    assert "123 NW 1st St" in sheet
