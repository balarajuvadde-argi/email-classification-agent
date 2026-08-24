from email_classification_agent.models import ParsedEmail
from email_classification_agent.property_appraiser import MiamiDadePropertyClient


class _FakePropertyClient(MiamiDadePropertyClient):
    def __init__(self):
        super().__init__()
        self.requests = []

    def _request(self, params):
        self.requests.append(params)
        if params["Operation"] == "GetAddress":
            return {
                "Completed": True,
                "MinimumPropertyInfos": [
                    {
                        "Municipality": "Unincorporated County",
                        "SiteAddress": params["myAddress"],
                        "Strap": "30-2123-006-0760",
                    }
                ],
            }
        return {
            "Completed": True,
            "PropertyInfo": {
                "DORDescription": "RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
                "LotSize": "12000",
            },
            "LegalDescription": {
                "Description": "HYDE PARK MANOR LOT 12 AND 13 BLK 5"
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
        _message("622 S C St Lake Worth Beach 33460\n1310 NW 6th Ave Florida City 33034")
    )

    assert len(records) == 2
    assert all(record.folio.startswith("30-") for record in records)
    assert all(record.qualifies for record in records)
    assert len(client.requests) == 4


def test_property_lookup_fails_closed_when_address_is_unknown():
    class _NoMatch(MiamiDadePropertyClient):
        def _request(self, params):
            return {"Completed": True, "MinimumPropertyInfos": []}

    records = _NoMatch().lookup_email(_message("1310 NW 6th Ave Florida City 33034"))

    assert records[0].lookup_status == "address_not_found"
    assert records[0].qualifies is False
