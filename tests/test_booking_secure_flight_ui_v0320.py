from pathlib import Path


SCRIPT = Path("app/web/assets/booking-passengers.js").read_text(
    encoding="utf-8"
)


def _gender_select_block() -> str:
    marker = '<span>Género *</span>'
    start = SCRIPT.index(marker)
    end = SCRIPT.index("</select>", start)
    return SCRIPT[start:end]


def test_gender_is_required_in_booking_passenger_ui() -> None:
    block = _gender_select_block()

    assert 'data-field="gender"' in block
    assert "required" in block
    assert '<option value="">Seleccionar</option>' in block
    assert 'value="M"' in block
    assert 'value="F"' in block
    assert 'value="X"' in block
    assert "No informado" not in block


def test_browser_payload_already_transports_gender() -> None:
    payload_start = SCRIPT.index("function collectPassengerPayload()")
    payload_end = SCRIPT.index(
        "\n  async function api",
        payload_start,
    )
    payload = SCRIPT[payload_start:payload_end]

    assert 'gender: fieldValue(passenger.slot_index, "gender")' in payload
