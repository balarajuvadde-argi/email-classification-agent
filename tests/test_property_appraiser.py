from email_classification_agent.models import ParsedEmail
from email_classification_agent.property_appraiser import (
    MiamiDadePropertyClient,
    _clean_address_query,
)


class _FakePropertyClient(MiamiDadePropertyClient):
    def __init__(self, *, folio="30-2123-006-0760", municipality="Unincorporated County", legal="LOTS 4 & 5 BLK 2", land_use="RESIDENTIAL - SINGLE FAMILY : 1 UNIT"):
        super().__init__()
        self.requests = []
        self._folio = folio
        self._municipality = municipality
        self._legal = legal
        self._land_use = land_use

    def _request(self, params):
        self.requests.append(params)
        if params["Operation"] == "GetAddress":
            return {
                "Completed": True,
                "MinimumPropertyInfos": [
                    {
                        "Municipality": self._municipality,
                        "SiteAddress": params["myAddress"],
                        "Strap": self._folio,
                    }
                ],
            }
        return {
            "Completed": True,
            "PropertyInfo": {
                "DORDescription": self._land_use,
                "LotSize": "12000",
            },
            "LegalDescription": {
                "Description": self._legal,
            },
        }


def _message(body: str) -> ParsedEmail:
    return ParsedEmail(
        message_id="m1",
        thread_id="t1",
        label_ids=("INBOX",),
        internal_date_ms=0,
        from_header="Vendor <vendor@example.com>",
        sender_header="Vendor <vendor@example.com>",
        reply_to_header="",
        to_header="user@example.com",
        subject="Wholesale opportunities",
        body_text=body,
    )


def test_property_lookup_extracts_addresses_and_qualifies_verified_record():
    client = _FakePropertyClient()

    records = client.lookup_email(
        _message("622 S C St Lake Worth Beach 33034 $250,000\n1310 NW 6th Ave Florida City 33034 $250,000")
    )

    assert len(records) == 2
    assert all(record.folio.startswith("30-") for record in records)
    assert all(record.is_folio_30 for record in records)
    assert all(record.has_double_lot for record in records)
    assert all(record.qualifies for record in records)
    assert len(client.requests) == 4


def test_multifamily_blast_pairs_each_property_with_asking_price():
    client = _FakePropertyClient()

    records = client.lookup_email(
        _message(
            """
            MULTI FAMILY Deals
            NEW OFF MARKET
            MULTI FAMILY!
            2493 NW 91st St
            Miami 33147
            Asking Price: $685,000
            Lot: 8,461

            DUPLEX!
            814 42nd St
            West Palm Beach 33407
            Asking Price: $390,000

            DUPLEX!
            88/90 NE 68th Ter
            Miami 33138
            Asking Price: $515,000

            DUPLEX!
            1100-1102 NW 57th St
            Miami 33127
            Asking Price: $500,000

            DUPLEX!
            1173-1175 NW 65th St�
            �Miami 33150
            Asking Price: $495,000
            """
        )
    )

    assert [record.address for record in records] == [
        "2493 NW 91st St",
        "90 NE 68th Ter",
        "1102 NW 57th St",
        "1175 NW 65th St",
    ]
    assert [record.asking_price for record in records] == [
        685_000,
        515_000,
        500_000,
        495_000,
    ]


def test_asking_price_is_preferred_over_arv_and_other_amounts():
    client = _FakePropertyClient()

    records = client.lookup_email(
        _message(
            "15141 SW 50th Ter, Miami, Florida, 33185\n"
            "Asking $545,900\n"
            "ARV $850,000\n"
            "3 Beds / 2 Baths"
        )
    )

    assert records[0].asking_price == 545_900


def test_inline_property_block_can_still_find_asking_price():
    client = _FakePropertyClient()

    records = client.lookup_email(
        _message(
            "OFF MARKET MULTI FAMILY! 1310 NW 6th Ave fter upda Falorida City 33034 "
            "Asking Price: $2,350,000 Sq. Ft: 8,597 Lot: 18,290 Yr. Built: 2023"
        )
    )

    assert len(records) == 1
    assert records[0].address == "1310 NW 6th Ave"
    assert records[0].asking_price == 2_350_000


def test_per_door_subject_price_is_not_used_and_duplicate_address_is_skipped():
    client = _FakePropertyClient()

    records = client.lookup_email(
        _message(
            """
            Fwd: 7 Units in Miami — $125K/Door in Opportunity Zone
            1133 NW 79 TER MIAMI, FL 33150
            Call Michelle at 954-256-2305
            Only $ 879,900
            BEST PRICE AVAILABLE PER DOOR IN ZIP CODE
            Type: Multi Family - 7 Units

            1133 NW 79th Ter,
            Miami FL 33150
            Only $ 879,900
            BEST PRICE AVAILABLE PER DOOR IN ZIP CODE
            """
        )
    )

    assert len(records) == 1
    assert records[0].address == "1133 NW 79 TER"
    assert records[0].asking_price == 879_900


def test_single_lot_block_number_is_not_double_lot_or_target_match():
    client = _FakePropertyClient(
        legal=(
            "SEMINOLE LAWN PB 9-171 "
            "LOT 1 BLK 2 "
            "LOT SIZE 50.000 X 110 "
            "OR 16289-0427 0394 4"
        )
    )

    records = client.lookup_email(_message("3300 NW 50th St Miami FL 33142 $419,000"))

    assert len(records) == 1
    assert records[0].folio.startswith("30-")
    assert records[0].has_double_lot is False
    assert records[0].qualifies is False
    assert any("does not show multiple lot numbers" in r for r in records[0].reasons)
    assert any("above $275,000 target" in r for r in records[0].reasons)


def test_double_lot_above_price_target_is_not_target_match():
    client = _FakePropertyClient(legal="SEMINOLE LAWN PB 9-171 LOTS 4 & 5 BLK 2")

    records = client.lookup_email(_message("16225 NE 2nd Ave Miami FL 33162 $362,500"))

    assert len(records) == 1
    assert records[0].has_double_lot is True
    assert records[0].qualifies is False
    assert any("above $275,000 target" in r for r in records[0].reasons)


def test_price_target_can_be_changed_from_environment(monkeypatch):
    monkeypatch.setenv("ACQUISITION_PRICE_TARGET", "400000")
    client = _FakePropertyClient(legal="SEMINOLE LAWN PB 9-171 LOTS 4 & 5 BLK 2")

    records = client.lookup_email(_message("16225 NE 2nd Ave Miami FL 33162 $362,500"))

    assert records[0].qualifies is True
    assert any("at or below $400,000 target" in r for r in records[0].reasons)


def test_double_lot_requirement_can_be_changed_from_environment(monkeypatch):
    monkeypatch.setenv("ACQUISITION_REQUIRE_DOUBLE_LOT", "false")
    client = _FakePropertyClient(legal="SEMINOLE LAWN PB 9-171 LOT 1 BLK 2")

    records = client.lookup_email(_message("3300 NW 50th St Miami FL 33142 $250,000"))

    assert records[0].has_double_lot is False
    assert records[0].qualifies is True


def test_excluded_municipalities_can_be_changed_from_environment(monkeypatch):
    monkeypatch.setenv("ACQUISITION_EXCLUDED_MUNICIPALITIES", "UNINCORPORATED COUNTY")
    client = _FakePropertyClient()

    records = client.lookup_email(_message("16225 NE 2nd Ave Miami FL 33162 $250,000"))

    assert records[0].qualifies is False
    assert any("excluded municipality: UNINCORPORATED COUNTY" in r for r in records[0].reasons)


def test_plural_lots_with_separator_is_double_lot():
    client = _FakePropertyClient(
        legal=(
            "SEMINOLE LAWN PB 9-171 "
            "LOTS 4 & 5 BLK 2 "
            "LOT SIZE 100.000 X 110 "
            "OR 13845-0483 0988 3"
        )
    )

    records = client.lookup_email(_message("16225 NE 2nd Ave Miami FL 33162 $250,000"))

    assert len(records) == 1
    assert records[0].has_double_lot is True
    assert records[0].qualifies is True


def test_clean_address_query_removes_leading_noise():
    raw = "2888 for more information 16225 NE 2nd Ave"
    cleaned = _clean_address_query(raw)
    assert cleaned == "16225 NE 2nd Ave"

    raw2 = "call 305-555-1234 for details 11370 SW 224th St"
    cleaned2 = _clean_address_query(raw2)
    assert cleaned2 == "11370 SW 224th St"


def test_property_lookup_non_30_folio_does_not_qualify():
    client = _FakePropertyClient(folio="04-2132-013-1230", municipality="HIALEAH")
    records = client.lookup_email(_message("890 E 52nd St Miami FL $200,000"))

    assert len(records) == 1
    assert records[0].is_folio_30 is False
    assert records[0].qualifies is False
    assert any("does not start with 30" in r for r in records[0].reasons)


def test_property_lookup_excluded_municipality_does_not_qualify():
    client = _FakePropertyClient(folio="08-2122-014-0540", municipality="OPA-LOCKA")
    records = client.lookup_email(_message("14241 NW 23rd Pl Opa-locka FL $150,000"))

    assert len(records) == 1
    assert records[0].qualifies is False
    assert any("excluded municipality: OPA-LOCKA" in r for r in records[0].reasons)


def test_property_lookup_fails_closed_when_address_is_unknown():
    class _NoMatch(MiamiDadePropertyClient):
        def _request(self, params):
            return {"Completed": True, "MinimumPropertyInfos": []}

    records = _NoMatch().lookup_email(_message("1310 NW 6th Ave Florida City 33034"))

    assert records[0].lookup_status == "address_not_found"
    assert records[0].qualifies is False
