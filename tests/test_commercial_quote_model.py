from decimal import Decimal

from app.models.commercial_quote import (
    CommercialFare, CommercialFareRules, CommercialOption,
    CommercialPassengerPrice, CommercialQuote,
)
from app.models.itinerary import FlightSegment
from app.models.quote_request import SearchLeg


def test_commercial_quote_serializes_canonical_structure():
    quote = CommercialQuote(
        quote_id='Q-TEST', environment='CERT', trip_type='round_trip',
        legs=[
            SearchLeg(origin='EZE', destination='MIA', departure_date='2026-09-19'),
            SearchLeg(origin='MIA', destination='EZE', departure_date='2026-09-30'),
        ],
        options=[
            CommercialOption(
                rank=1, score=Decimal('100'), stops=0, duration_minutes=540,
                commercial_labels=['recommended'],
                segments=[FlightSegment(
                    marketing_carrier='AA', flight_number='908',
                    departure_airport='EZE', arrival_airport='MIA',
                    departure_at='2026-09-19T22:15:00',
                    arrival_at='2026-09-20T06:20:00',
                )],
                fares=[CommercialFare(
                    cabin='economy', currency='USD',
                    brand_name='MAIN CABIN', brand_code='MAIN',
                    price_per_passenger=Decimal('1143.33'),
                    total_price=Decimal('2286.66'),
                    passenger_prices=[
                        CommercialPassengerPrice(
                            passenger_type='ADT', quantity=1, currency='USD',
                            unit_price=Decimal('1143.33'), total_price=Decimal('1143.33'),
                        ),
                        CommercialPassengerPrice(
                            passenger_type='C10', quantity=1, age=10, currency='USD',
                            unit_price=Decimal('1143.33'), total_price=Decimal('1143.33'),
                        ),
                    ],
                    fare_basis_codes=['QLN0AHM1'], validating_carrier='AA',
                    rules=CommercialFareRules(
                        baggage='1 pieza despachada de hasta 23 kg.',
                        changes='Cambios permitidos con diferencia tarifaria.',
                        refunds='Devolución no permitida.',
                        no_show='No-show no permitido.',
                    ),
                )],
            )
        ],
    )
    data = quote.model_dump(mode='json')
    assert data['quote_id'] == 'Q-TEST'
    assert data['trip_type'] == 'round_trip'
    assert len(data['legs']) == 2
    assert data['options'][0]['rank'] == 1
    assert data['options'][0]['fares'][0]['brand_name'] == 'MAIN CABIN'
    assert data['options'][0]['fares'][0]['passenger_prices'][1]['passenger_type'] == 'C10'
    assert data['options'][0]['fares'][0]['rules']['refunds'] == 'Devolución no permitida.'


def test_commercial_fare_keeps_booking_identity_fields():
    fare = CommercialFare(
        cabin='business', currency='USD', brand_name='FLAGSHIP BUSINESS',
        brand_code='FBUS', price_per_passenger=Decimal('4061.23'),
        fare_basis_codes=['ILN8NHF1'], validating_carrier='AA',
    )
    assert fare.fare_basis_codes == ['ILN8NHF1']
    assert fare.validating_carrier == 'AA'
    assert fare.brand_code == 'FBUS'
