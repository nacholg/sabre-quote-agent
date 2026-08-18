from app.services.pricing_rules import PricingCurrency, pricing_modifier, resolve_pricing_currencies


def test_domestic_argentina_forces_ars():
    assert resolve_pricing_currencies("AEP", "COR", PricingCurrency.USD) == ["ARS"]
    assert resolve_pricing_currencies("EZE", "BRC", PricingCurrency.BOTH) == ["ARS"]


def test_international_defaults_usd_and_supports_both():
    assert resolve_pricing_currencies("EZE", "MIA", PricingCurrency.AUTO) == ["USD"]
    assert resolve_pricing_currencies("EZE", "MIA", PricingCurrency.ARS) == ["ARS"]
    assert resolve_pricing_currencies("EZE", "MIA", PricingCurrency.BOTH) == ["USD", "ARS"]


def test_pricing_modifiers():
    assert pricing_modifier("USD") == "MUSD"
    assert pricing_modifier("ARS") == "MARS"
