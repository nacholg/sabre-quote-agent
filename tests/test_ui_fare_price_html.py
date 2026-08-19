from pathlib import Path


def test_fare_price_html_has_no_self_recursion():
    html = Path('app/web/index.html').read_text(encoding='utf-8')
    assert 'return `${farePriceHtml(f)}`;' not in html
    assert 'money(f.price_per_passenger,f.currency)' in html


def test_fare_price_html_keeps_passenger_breakdown():
    html = Path('app/web/index.html').read_text(encoding='utf-8')
    assert 'const pax=f?.passenger_prices||[];' in html
    assert 'class="pax-price"' in html
    assert 'class="pax-total"' in html
