from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.itinerary import ItineraryOption
from app.services.quote_repository import QuoteRepository


OFFICIAL_REFERENCES = {
    "air_rules_soapui": (
        "SabreDevStudio/SabreAPIsWorkflows/"
        "SabreAPIsTestSuites/OTA_AirRulesLLSRQ-v2.3.0/"
        "OTA_AirRulesRQ_2.3.0-soapui-project.xml"
    ),
    "session_create_soapui": (
        "SabreDevStudio/SabreAPIsWorkflows/"
        "SabreAPIsTestSuites/SessionCreateRQ-v1.0.0/"
        "SessionCreateRQ-1.0.0-soapui-project.xml"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida preparación contractual para OTA_AirRulesLLSRQ en CERT."
    )
    parser.add_argument("--quote-id", required=True)
    parser.add_argument("--rank", type=int, default=1)
    parser.add_argument("--db", default="data/quotes.db")
    parser.add_argument("--category", type=int, default=16)
    parser.add_argument("--soap-endpoint", default=os.getenv("SABRE_SOAP_ENDPOINT"))
    parser.add_argument("--service-action", default=os.getenv("SABRE_AIR_RULES_ACTION"))
    parser.add_argument("--session-token", default=os.getenv("SABRE_SOAP_SESSION_TOKEN"))
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = QuoteRepository(Path(args.db))
    record = repo.get(args.quote_id)
    if record is None:
        raise SystemExit(f"Cotización no encontrada: {args.quote_id}")

    raw = next(
        (
            item for item in (record.quote_response.get("options") or [])
            if int(item.get("rank", 0)) == args.rank
        ),
        None,
    )
    if raw is None:
        raise SystemExit(f"Rank no encontrado: {args.rank}")

    option = ItineraryOption.model_validate(raw["itinerary"])
    fares = []
    for values in (option.fare_options_by_currency or {}).values():
        fares.extend(values)
    if not fares:
        fares = list((option.fares_by_currency or {}).values()) or [option.fare]

    components = []
    missing_component_fields = []
    for fare_idx, fare in enumerate(fares, start=1):
        for comp_idx, comp in enumerate(fare.branded_components, start=1):
            carrier = comp.governing_carrier or fare.validating_carrier
            row = {
                "fare_index": fare_idx,
                "brand_name": fare.brand_name,
                "component_index": comp_idx,
                "origin": comp.begin_airport,
                "destination": comp.end_airport,
                "fare_basis": comp.fare_basis_code,
                "governing_carrier": carrier,
                "vendor": comp.vendor_code,
                "tariff": comp.tariff,
                "rule_number": comp.rule_number,
            }
            components.append(row)
            for field in ("origin", "destination", "fare_basis", "governing_carrier"):
                if not row[field]:
                    missing_component_fields.append(
                        f"fare {fare_idx}/component {comp_idx}: {field}"
                    )

    contract = {
        "quote_id": args.quote_id,
        "rank": args.rank,
        "category": args.category,
        "fare_components": len(components),
        "component_minimum_ready": not missing_component_fields and bool(components),
        "missing_component_fields": missing_component_fields,
        "soap_contract": {
            "endpoint": args.soap_endpoint,
            "service_action": args.service_action,
            "session_token_present": bool(args.session_token),
            "ready": bool(
                args.soap_endpoint and args.service_action and args.session_token
            ),
        },
        "official_references": OFFICIAL_REFERENCES,
        "security": {
            "session_token_written_to_disk": False,
            "authorization_written_to_disk": False,
        },
        "transmission_enabled": False,
    }

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out/"air_rules_contract_probe.json"
    path.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Quote: {args.quote_id}")
    print(f"Rank: {args.rank}")
    print(f"Fare components: {len(components)}")
    print(f"Componentes mínimos listos: {'SI' if contract['component_minimum_ready'] else 'NO'}")
    print(f"SOAP endpoint: {'OK' if args.soap_endpoint else 'FALTA'}")
    print(f"Service action: {'OK' if args.service_action else 'FALTA'}")
    print(f"SOAP Session Token: {'OK' if args.session_token else 'FALTA'}")
    print(f"Contrato: {path}")

    if args.execute:
        if not contract["component_minimum_ready"]:
            print("BLOQUEADO: faltan datos mínimos de fare components.")
            return 2
        if not contract["soap_contract"]["ready"]:
            print(
                "BLOQUEADO: para transmitir hacen falta SOAP endpoint, service action "
                "y Session Token confirmados por Sabre."
            )
            return 3
        print(
            "BLOQUEADO POR DISEÑO: 0.16.2 valida el contrato pero todavía no construye "
            "ni transmite el envelope SOAP. La siguiente revisión lo habilitará una vez "
            "confirmados endpoint/action/session."
        )
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
