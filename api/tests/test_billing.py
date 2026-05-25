from app.services.billing import calc_cost_cents
from types import SimpleNamespace


def test_calc_cost_cents_basic():
    m = SimpleNamespace(prompt_rate=10000, completion_rate=20000)  # 1 cent / 1K tokens prompt
    # 1000 prompt + 1000 completion => 10000 + 20000 = 30000 micro-cents => 0.003 cents => ceil = 1
    assert calc_cost_cents(m, 1000, 1000) == 1


def test_calc_cost_cents_zero():
    m = SimpleNamespace(prompt_rate=0, completion_rate=0)
    assert calc_cost_cents(m, 1000, 1000) == 0
