from app.services.air_rules_structured_parser import (
    parse_category_16_structured,
)


def test_aa_nonrefundable_changes_allowed_fare_difference():
    text = """CANCELLATIONS
ANY TIME
TICKET IS NON-REFUNDABLE IN CASE OF CANCEL/NO-SHOW/REFUND.
CHANGES
BEFORE DEPARTURE
CHANGES PERMITTED.
WHEN THE NEW ITINERARY RESULTS IN A HIGHER FARE
THE DIFFERENCE WILL BE COLLECTED.
AFTER DEPARTURE
CHANGES PERMITTED.
"""
    parsed = parse_category_16_structured(text)
    assert parsed.cancellation_before_departure.status == "not_allowed"
    assert parsed.changes_before_departure.status == "allowed"
    assert parsed.changes_after_departure.status == "allowed"
    assert parsed.changes_before_departure.fare_difference_applies is True


def test_aa_flexible_refundable_any_time():
    text = """CANCELLATIONS
ANY TIME
CANCELLATIONS PERMITTED FOR CANCEL/NO-SHOW.
CANCELLATIONS ARE PERMITTED WITHIN TICKET VALIDITY
OF ORIGINAL TICKET.
FOR CANCELLATION AFTER DEPARTURE THE REFUND WILL
BE THE DIFFERENCE BETWEEN FARE PAID AND FARE FOR
JOURNEY TRAVELLED.
CHANGES
ANY TIME
CHANGES PERMITTED FOR NO-SHOW/REISSUE/REVALIDATION.
"""
    parsed = parse_category_16_structured(text)
    assert parsed.cancellation_before_departure.status == "allowed"
    assert parsed.cancellation_after_departure.status == "allowed"
    assert parsed.changes_before_departure.status == "allowed"
    assert parsed.changes_after_departure.status == "allowed"


def test_change_fee_amount_is_contextualized():
    text = """CANCELLATIONS
ANY TIME
REFUND PERMITTED.
CHANGES
BEFORE DEPARTURE
CHANGES PERMITTED.
CHANGE FEE EUR 200.00.
"""
    parsed = parse_category_16_structured(text)
    detail = parsed.changes_before_departure
    assert detail.status == "with_fee"
    assert str(detail.amount) == "200.00"
    assert detail.currency == "EUR"


def test_unticketed_agency_fee_is_not_passenger_penalty():
    text = """CANCELLATIONS
ANY TIME
CANCELLATIONS PERMITTED.
FOR TRAVEL AGENCY BOOKINGS AA WILL ASSESS A USD 50.00 FEE
ON ANY UNTICKETED RESERVATION NOT CANCELLED BEFORE DEPARTURE.
CHANGES
ANY TIME
CHANGES PERMITTED.
"""
    parsed = parse_category_16_structured(text)
    assert parsed.cancellation_before_departure.amount is None
