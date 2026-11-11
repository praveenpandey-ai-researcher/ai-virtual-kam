"""Tests for the diagnosis engine. Run: python -m pytest -q"""
import copy

import pytest

from app.diagnosis import diagnose, format_inr, load_seller_data, spoken_inr


@pytest.fixture
def data():
    return load_seller_data()


@pytest.fixture
def result(data):
    return diagnose(data)


# ---------------------------------------------------------------- formatting
@pytest.mark.parametrize("amount,expected", [
    (500, "₹500"), (65000, "₹65,000"), (135000, "₹1,35,000"), (200000, "₹2,00,000"), (12345678, "₹1,23,45,678"),
])
def test_format_inr(amount, expected):
    assert format_inr(amount) == expected


@pytest.mark.parametrize("amount,expected", [
    (135000, "1 lakh 35 thousand rupees"), (65000, "65 thousand rupees"),
    (200000, "2 lakh rupees"), (8100, "8 thousand 100 rupees"),
])
def test_spoken_inr(amount, expected):
    assert spoken_inr(amount) == expected


# ---------------------------------------------------------------- sample data (brief)
def test_brief_numbers_are_preserved(data):
    assert data["metrics"]["last_month"]["sales"] == 200000
    assert data["metrics"]["this_month"]["orders"] == 270
    assert len(data["illustrative"]["newly_out_of_stock"]) == (
        data["metrics"]["this_month"]["out_of_stock_products"] - data["metrics"]["last_month"]["out_of_stock_products"])


def test_derived_metrics(result):
    d = result["derived"]
    assert d["sales_lost"] == 65000
    assert d["sales_change_pct"] == -32.5
    assert d["orders_change_pct"] == -32.5
    assert d["asp_change_pct"] == 0
    assert d["cancelled_orders_last"] == 32
    assert d["cancelled_orders_now"] == 38
    assert d["delivered_value_last"] == 184000
    assert d["delivered_value_now"] == 116100
    assert d["live_products_last"] == 45 and d["live_products_now"] == 32
    assert d["orders_per_live_product_last"] == 8.9
    assert d["orders_per_live_product_now"] == 8.4


def test_inventory_is_top_cause(result):
    top = result["findings"][0]
    assert top["id"] == "inventory"
    assert top["estimated_lost_orders"] == 113
    assert top["estimated_monthly_impact_inr"] == 56500


def test_revenue_bridge_adds_up(result):
    b = result["revenue_bridge"]
    assert b["lost_to_stock_outs"] == 56500 and b["lost_other"] == 8500
    assert b["this_month_sales"] + b["lost_to_stock_outs"] + b["lost_other"] == b["last_month_sales"]


def test_pricing_ruled_out(result):
    assert [r["id"] for r in result["ruled_out"]] == ["pricing"]
    assert "pricing" not in [f["id"] for f in result["findings"]]


def test_cancellation_impact(result):
    f = next(f for f in result["findings"] if f["id"] == "cancellations")
    assert f["estimated_monthly_impact_inr"] == 8100


def test_three_recommendations_in_priority_order(result):
    recs = result["recommendations"]
    assert [r["id"] for r in recs] == ["R1", "R2", "R3"]
    assert recs[0]["priority_products"][0] == "Stainless Steel Water Bottle 1L"


def test_headline(result):
    assert result["headline"] == "Sales fell 32.5% (₹65,000). Main cause: products running out of stock."


# ---------------------------------------------------------------- the rules generalise
def test_price_change_is_detected(data):
    d = copy.deepcopy(data)
    d["metrics"]["this_month"]["average_selling_price"] = 450
    r = diagnose(d)
    assert "pricing" in [f["id"] for f in r["findings"]]
    assert r["ruled_out"] == []


def test_healthy_month_has_no_findings(data):
    d = copy.deepcopy(data)
    d["metrics"]["this_month"] = dict(d["metrics"]["last_month"])
    d["illustrative"]["newly_out_of_stock"] = []
    r = diagnose(d)
    assert r["findings"] == []
    assert r["recommendations"] == []


def test_inventory_estimate_without_product_list(data):
    d = copy.deepcopy(data)
    d["illustrative"]["newly_out_of_stock"] = []
    f = next(f for f in diagnose(d)["findings"] if f["id"] == "inventory")
    assert f["estimated_lost_orders"] == round(13 * 8.9)  # falls back to catalogue average
