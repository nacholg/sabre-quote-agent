from pathlib import Path

HTML = Path("app/web/index.html").read_text(encoding="utf-8")


def test_v029c1_route_summary_uses_requested_legs_not_endpoints_only():
    assert "function resultRequestedLegs()" in HTML
    assert "function resultSegmentDate(segment)" in HTML
    assert "const requestedLeg=requested[index]||{};" in HTML
    assert "requestedLeg.origin||first.departure_airport" in HTML
    assert "requestedLeg.destination||last.arrival_airport" in HTML
    assert "segmentDate>=nextDate" in HTML


def test_v029c1_detail_flights_stack_vertically():
    assert 'id="patagonik-v029c1-fare-choice"' in HTML
    assert ".option-details-body > .flight{" in HTML
    assert "display:block!important;" in HTML
    assert "width:100%;" in HTML


def test_v029c1_fare_cards_are_clickable_and_update_summary():
    assert "function installFareChoiceInteractions(options)" in HTML
    assert "function selectFareForResult(rank,fareIndex)" in HTML
    assert "function updateResultFareSummary(rank,fareIndex)" in HTML
    assert "selectedFareIndexByRank" in HTML
    assert "fare.total_price??fare.price_per_passenger" in HTML
    assert ".result-fare-name" in HTML
    assert ".result-baggage-main" in HTML
    assert ".result-changes-value" in HTML
    assert ".result-refunds-value" in HTML


def test_v029c1_result_cards_are_addressable_by_rank():
    assert 'data-option-rank="${item.rank}"' in HTML
    assert "installFareChoiceInteractions(options);" in HTML


def test_v029c1_selected_fare_has_visual_state():
    assert ".fare.fare-selected{" in HTML
    assert 'content:"Mostrando";' in HTML
    assert "fareCard.setAttribute(" in HTML
    assert '"aria-pressed"' in HTML
    assert 'index===fareIndex?"true":"false"' in HTML
