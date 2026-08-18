from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.itinerary import FareOption, ItineraryOption
from app.services.quote_repository import QuoteRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnóstico v0.16.1: extrae fare components y prepara Category 16."
    )
    parser.add_argument("--quote-id", required=True)
    parser.add_argument("--rank", type=int, default=1)
    parser.add_argument("--category", type=int, default=16)
    parser.add_argument("--db", default="data/quotes.db")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Reservado para la siguiente fase. 0.16.1 no transmite SOAP a Sabre.",
    )
    return parser.parse_args()


def pick_fares(option: ItineraryOption) -> list[FareOption]:
    fares: list[FareOption] = []
    for values in (option.fare_options_by_currency or {}).values():
        fares.extend(values)
    if not fares:
        fares = list((option.fares_by_currency or {}).values())
    return fares or [option.fare]


def component_dict(fare: FareOption, index: int) -> dict:
    component = fare.branded_components[index]
    carrier = component.governing_carrier or fare.validating_carrier
    if not carrier:
        # Useful diagnostic fallback, but explicitly marked as inferred.
        carrier_source = "missing"
    else:
        carrier_source = (
            "fare_component" if component.governing_carrier else "validating_carrier_fallback"
        )
    return {
        "component_index": index + 1,
        "component_ref": component.component_ref,
        "begin_airport": component.begin_airport,
        "end_airport": component.end_airport,
        "fare_basis_code": component.fare_basis_code,
        "governing_carrier": carrier,
        "governing_carrier_source": carrier_source,
        "vendor_code": component.vendor_code,
        "tariff": component.tariff,
        "rule_number": component.rule_number,
        "fare_amount": str(component.fare_amount) if component.fare_amount is not None else None,
        "fare_currency": component.fare_currency,
        "brand_code": component.brand_code,
        "brand_name": component.brand_name,
    }


def build_candidate_xml(component: dict, category: int, departure_date: str | None) -> str:
    """Diagnostic XML only.

    We intentionally do not claim this is executable OTA_AirRulesLLSRQ yet.
    Exact Sabre SOAP envelope/session/action/schema must be validated against
    the Sabre contract enabled for the PCC before transmission.
    """
    root = Element("FareRuleDiagnostic")
    root.set("category", str(category))
    root.set("status", "NOT_FOR_TRANSMISSION")
    SubElement(root, "Carrier").text = component.get("governing_carrier") or ""
    SubElement(root, "FareBasis").text = component.get("fare_basis_code") or ""
    SubElement(root, "Origin").text = component.get("begin_airport") or ""
    SubElement(root, "Destination").text = component.get("end_airport") or ""
    SubElement(root, "DepartureDate").text = departure_date or ""
    SubElement(root, "Vendor").text = component.get("vendor_code") or ""
    SubElement(root, "Tariff").text = component.get("tariff") or ""
    SubElement(root, "Rule").text = component.get("rule_number") or ""
    return tostring(root, encoding="unicode")


def main() -> int:
    args = parse_args()
    if args.execute:
        print(
            "BLOQUEADO: v0.16.1 es diagnóstica. "
            "Todavía no transmitimos OTA_AirRulesLLSRQ hasta validar endpoint, "
            "SOAP Action, sesión y schema exactos con Sabre."
        )
        return 2

    repo = QuoteRepository(Path(args.db))
    record = repo.get(args.quote_id)
    if record is None:
        raise SystemExit(f"Cotización no encontrada: {args.quote_id}")

    raw_option = next(
        (
            item for item in (record.quote_response.get("options") or [])
            if int(item.get("rank", 0)) == args.rank
        ),
        None,
    )
    if raw_option is None:
        raise SystemExit(f"Rank no encontrado: {args.rank}")

    option = ItineraryOption.model_validate(raw_option["itinerary"])
    fares = pick_fares(option)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    departure_date = (
        option.segments[0].departure_at.date().isoformat()
        if option.segments else None
    )
    products = []
    xml_candidates = []
    missing = set()

    for fare_index, fare in enumerate(fares, start=1):
        components = []
        for idx in range(len(fare.branded_components)):
            item = component_dict(fare, idx)
            components.append(item)
            for key in ("begin_airport", "end_airport", "fare_basis_code", "governing_carrier"):
                if not item.get(key):
                    missing.add(key)
            xml_candidates.append(
                {
                    "fare_index": fare_index,
                    "brand_name": fare.brand_name,
                    "currency": fare.currency,
                    "component_index": idx + 1,
                    "diagnostic_xml": build_candidate_xml(item, args.category, departure_date),
                }
            )
        products.append(
            {
                "fare_index": fare_index,
                "brand_name": fare.brand_name,
                "brand_code": fare.brand_code,
                "currency": fare.currency,
                "fare_basis_codes": fare.fare_basis_codes,
                "validating_carrier": fare.validating_carrier,
                "components": components,
            }
        )

    components_path = out / "fare_rule_components.json"
    components_path.write_text(
        json.dumps(
            {
                "quote_id": args.quote_id,
                "rank": args.rank,
                "category": args.category,
                "products": products,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    xml_path = out / "ota_air_rules_request.xml"
    xml_path.write_text(
        "\n\n".join(x["diagnostic_xml"] for x in xml_candidates)
        or "<!-- No fare components available -->",
        encoding="utf-8",
    )
    diagnostics = {
        "quote_id": args.quote_id,
        "rank": args.rank,
        "category": args.category,
        "fare_products": len(products),
        "fare_components": sum(len(x["components"]) for x in products),
        "missing_required_fields": sorted(missing),
        "ready_for_contract_validation": not missing and bool(xml_candidates),
        "transmission_enabled": False,
        "important": (
            "ota_air_rules_request.xml es un artefacto diagnóstico y NO un request "
            "SOAP ejecutable. Falta validar contrato Sabre: endpoint, namespace/schema, "
            "SOAPAction y mecanismo de sesión/contexto."
        ),
    }
    diag_path = out / "fare_rules_diagnostics.json"
    diag_path.write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Quote: {args.quote_id}")
    print(f"Rank: {args.rank}")
    print(f"Category: {args.category}")
    print(f"Productos tarifarios: {len(products)}")
    print(f"Fare components: {diagnostics['fare_components']}")
    print(f"Campos faltantes: {', '.join(sorted(missing)) if missing else 'ninguno'}")
    print(f"Components: {components_path}")
    print(f"XML diagnóstico: {xml_path}")
    print(f"Diagnóstico: {diag_path}")
    print("Transmisión Sabre: BLOQUEADA en v0.16.1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
