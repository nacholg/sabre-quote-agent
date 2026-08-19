from __future__ import annotations

from app.models.api import FareRuleAuditResponse


def prepare_fare_rule_response(
    response: FareRuleAuditResponse,
    *,
    include_source_text: bool = False,
) -> FareRuleAuditResponse:
    if include_source_text:
        return response

    options = []
    for option in response.options:
        fares = []
        for fare in option.fares:
            details = fare.structured_details
            if details is not None:
                updates = {}
                for field_name in (
                    "changes_before_departure",
                    "changes_after_departure",
                    "cancellation_before_departure",
                    "cancellation_after_departure",
                    "no_show",
                ):
                    detail = getattr(details, field_name)
                    if detail is not None:
                        updates[field_name] = detail.model_copy(
                            update={"source_text": None}
                        )
                details = details.model_copy(update=updates)

            fares.append(
                fare.model_copy(
                    update={"structured_details": details}
                )
            )

        options.append(
            option.model_copy(update={"fares": fares})
        )

    return response.model_copy(update={"options": options})
