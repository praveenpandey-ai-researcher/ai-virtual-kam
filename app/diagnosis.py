"""Deterministic diagnosis engine.

Design decision (PRD section 11): every number the voice agent says comes from this
module, never from the LLM. The LLM explains and discusses these results; it does not
compute them. This keeps the advice accurate, testable and explainable.

The rules are generic (thresholds on month-over-month changes), so the same engine
works for any seller with the same metric shape, not only the sample data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).parent / "data" / "seller_data.json"

# Thresholds for the rules. Kept in one place so they are easy to explain and tune.
PRICE_CHANGE_TOLERANCE = 0.02          # ASP change under 2% => pricing ruled out
OOS_INCREASE_MIN = 3                   # 3+ more out-of-stock products => inventory issue
CANCELLATION_INCREASE_MIN_PTS = 0.02   # +2 percentage points => cancellation issue
FLAT_DEMAND_TOLERANCE = 0.10           # orders per live product within 10% => demand is stable


# ---------------------------------------------------------------- formatting helpers
def format_inr(amount: float) -> str:
    """Indian digit grouping: 135000 -> '₹1,35,000'."""
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign}₹{s}"


def spoken_inr(amount: float) -> str:
    """How a person would say it on a call: 135000 -> '1 lakh 35 thousand rupees'."""
    n = int(round(abs(amount)))
    lakhs, rest = divmod(n, 100_000)
    thousands, units = divmod(rest, 1000)
    parts = []
    if lakhs:
        parts.append(f"{lakhs} lakh")
    if thousands:
        parts.append(f"{thousands} thousand")
    if units or not parts:
        parts.append(str(units))
    return " ".join(parts) + " rupees"


def pct_change(old: float, new: float) -> float:
    return (new - old) / old if old else 0.0


# ---------------------------------------------------------------- data access
def load_seller_data(path: Path | str = DATA_FILE) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- diagnosis
def diagnose(data: dict[str, Any]) -> dict[str, Any]:
    last = data["metrics"]["last_month"]
    now = data["metrics"]["this_month"]
    catalogue = data.get("assumptions", {}).get("A3_active_catalogue_size")
    newly_oos = data.get("illustrative", {}).get("newly_out_of_stock", [])

    asp = now["average_selling_price"]
    sales_lost = last["sales"] - now["sales"]

    # --- derived metrics
    cancelled_last = last["orders"] * last["cancellation_rate"]
    cancelled_now = now["orders"] * now["cancellation_rate"]
    delivered_value_last = (last["orders"] - cancelled_last) * last["average_selling_price"]
    delivered_value_now = (now["orders"] - cancelled_now) * asp

    derived: dict[str, Any] = {
        "sales_lost": sales_lost,
        "sales_change_pct": round(pct_change(last["sales"], now["sales"]) * 100, 1),
        "orders_change_pct": round(pct_change(last["orders"], now["orders"]) * 100, 1),
        "asp_change_pct": round(pct_change(last["average_selling_price"], asp) * 100, 1),
        "cancelled_orders_last": round(cancelled_last),
        "cancelled_orders_now": round(cancelled_now),
        "delivered_value_last": round(delivered_value_last),
        "delivered_value_now": round(delivered_value_now),
        "delivered_value_change_pct": round(pct_change(delivered_value_last, delivered_value_now) * 100, 1),
    }
    if catalogue:
        live_last = catalogue - last["out_of_stock_products"]
        live_now = catalogue - now["out_of_stock_products"]
        derived.update({
            "live_products_last": live_last,
            "live_products_now": live_now,
            "live_products_change_pct": round(pct_change(live_last, live_now) * 100, 1),
            "orders_per_live_product_last": round(last["orders"] / live_last, 1),
            "orders_per_live_product_now": round(now["orders"] / live_now, 1),
        })

    findings: list[dict[str, Any]] = []
    ruled_out: list[dict[str, Any]] = []

    # --- Rule 1: pricing
    if abs(derived["asp_change_pct"]) / 100 < PRICE_CHANGE_TOLERANCE:
        ruled_out.append({
            "id": "pricing",
            "title": "Pricing is not the cause",
            "evidence": f"Average selling price is unchanged at {format_inr(asp)}, and orders fell by the "
                        f"same {abs(derived['orders_change_pct'])}% as sales, so the entire drop is fewer "
                        f"orders, not lower prices.",
            "implication": "Do not cut prices: it would reduce margin without fixing the real cause.",
        })
    else:
        findings.append({
            "id": "pricing",
            "title": "Average selling price changed",
            "severity": "medium",
            "confidence": "medium",
            "evidence": [f"Average selling price moved {derived['asp_change_pct']}%."],
            "explanation": "A price change can shift conversion and order volume.",
            "estimated_monthly_impact_inr": None,
            "questions_to_ask": ["Did you change prices, or did competitors change theirs?"],
        })

    # --- Rule 2: inventory / stock-outs
    oos_increase = now["out_of_stock_products"] - last["out_of_stock_products"]
    inventory_impact = None
    if oos_increase >= OOS_INCREASE_MIN:
        if newly_oos:
            lost_orders = sum(p["orders_last_month"] for p in newly_oos)
            basis = "orders these products received last month while in stock"
        else:
            per_product = derived.get("orders_per_live_product_last") or 0
            lost_orders = round(oos_increase * per_product)
            basis = "extra out-of-stock products x average orders per live product"
        inventory_impact = lost_orders * asp

        evidence = [
            f"Out-of-stock products rose from {last['out_of_stock_products']} to "
            f"{now['out_of_stock_products']} ({oos_increase} more).",
        ]
        if catalogue:
            evidence.append(
                f"Live products fell from {derived['live_products_last']} to {derived['live_products_now']} "
                f"({derived['live_products_change_pct']}%), close to the {derived['orders_change_pct']}% drop "
                f"in orders (assumes a catalogue of {catalogue} products).")
            per_last = derived["orders_per_live_product_last"]
            per_now = derived["orders_per_live_product_now"]
            if abs(pct_change(per_last, per_now)) <= FLAT_DEMAND_TOLERANCE:
                evidence.append(
                    f"Orders per live product stayed roughly flat ({per_last} to {per_now}): demand for "
                    f"listed products held up; fewer products were available to buy.")
        if newly_oos:
            top = sorted(newly_oos, key=lambda p: p["orders_last_month"], reverse=True)[:3]
            evidence.append("Top best-sellers now out of stock: " + ", ".join(
                f"{p['product']} ({p['orders_last_month']} orders last month)" for p in top) + ".")

        findings.append({
            "id": "inventory",
            "title": "Stock-outs are the main cause of the drop",
            "cause": "products running out of stock",
            "severity": "high",
            "confidence": "high",
            "evidence": evidence,
            "explanation": "Out-of-stock products cannot be bought and usually drop in search, so the seller "
                           "had less to sell. This also drives cancellations when stock runs out after an "
                           "order is placed.",
            "estimated_monthly_impact_inr": inventory_impact,
            "estimated_lost_orders": lost_orders,
            "top_products": sorted(newly_oos, key=lambda p: p["orders_last_month"], reverse=True)[:5],
            "impact_basis": basis,
            "questions_to_ask": [
                "Did you face a supply or restocking problem this month?",
                "Are your best-sellers among the products that ran out?",
            ],
        })

    # --- Rule 3: cancellations
    cancel_increase = now["cancellation_rate"] - last["cancellation_rate"]
    if cancel_increase >= CANCELLATION_INCREASE_MIN_PTS:
        excess_cancelled = now["orders"] * cancel_increase
        impact = excess_cancelled * asp
        linked = oos_increase >= OOS_INCREASE_MIN
        findings.append({
            "id": "cancellations",
            "title": f"Cancellations rose from {last['cancellation_rate']:.0%} to {now['cancellation_rate']:.0%}",
            "cause": "rising order cancellations",
            "severity": "high" if cancel_increase >= 0.05 else "medium",
            "confidence": "medium",
            "evidence": [
                f"Cancellation rate rose from {last['cancellation_rate']:.0%} to {now['cancellation_rate']:.0%}.",
                f"About {derived['cancelled_orders_now']} orders were cancelled this month vs "
                f"{derived['cancelled_orders_last']} last month, even though total orders fell.",
            ],
            "explanation": ("Likely linked to stock-outs: orders accepted for items that were no longer in "
                            "stock had to be cancelled. High cancellations can also hurt seller ranking."
                            if linked else
                            "Cancellations reduce delivered revenue and can hurt seller ranking."),
            "estimated_monthly_impact_inr": round(impact),
            "impact_basis": "extra cancellation rate x this month's orders x average selling price "
                            "(affects delivered revenue)",
            "questions_to_ask": [
                "Are most of these cancellations by you because stock ran out, or by customers?",
                "How often do you update stock counts on your listings?",
            ],
        })

    # --- Rule 4: visibility (inferred; no direct data)
    if any(f["id"] in ("inventory", "cancellations") for f in findings):
        unexplained = sales_lost - (inventory_impact or 0)
        findings.append({
            "id": "visibility",
            "title": "Likely knock-on drop in visibility",
            "severity": "medium",
            "confidence": "low",
            "evidence": [
                "No direct visibility data is available; this is inferred.",
                f"About {format_inr(max(unexplained, 0))} of the drop is not explained by the out-of-stock "
                f"products alone, which fits a ranking penalty from stock-outs and cancellations.",
            ],
            "explanation": "Marketplaces typically rank reliable, in-stock sellers higher, so stock-outs and "
                           "cancellations can reduce visibility of the seller's other products too.",
            "estimated_monthly_impact_inr": None,
            "questions_to_ask": ["Have you noticed fewer views or clicks on your in-stock products?"],
        })

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_rank[f["severity"]], -(f["estimated_monthly_impact_inr"] or 0)))

    recommendations = _recommend(findings, newly_oos)
    headline = _headline(findings, derived)

    # Where the lost sales went (for the on-screen chart): last month = this month + losses.
    explained = min(inventory_impact or 0, max(sales_lost, 0))
    revenue_bridge = {
        "last_month_sales": last["sales"],
        "this_month_sales": now["sales"],
        "lost_to_stock_outs": explained,
        "lost_other": max(sales_lost - explained, 0),
    }

    return {
        "seller": data["seller"],
        "metrics": {"last_month": last, "this_month": now},
        "derived": derived,
        "headline": headline,
        "revenue_bridge": revenue_bridge,
        "findings": findings,
        "ruled_out": ruled_out,
        "recommendations": recommendations,
        "assumptions": [
            "Sales is gross order value (orders x average price), before cancellations.",
            f"The seller has about {catalogue} active products." if catalogue else None,
            "Product-level data is illustrative mock data.",
        ],
        "notes_for_agent": [
            "Use only these numbers. Say 'about' or 'roughly' for estimates.",
            "Say amounts the Indian way, e.g. '1 lakh 35 thousand rupees'.",
        ],
    }


def _recommend(findings: list[dict[str, Any]], newly_oos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {f["id"]: f for f in findings}
    recs: list[dict[str, Any]] = []

    if "inventory" in by_id:
        f = by_id["inventory"]
        top = [p["product"] for p in sorted(newly_oos, key=lambda p: p["orders_last_month"], reverse=True)[:3]]
        recs.append({
            "id": "R1",
            "action": "Restock the out-of-stock products, best-sellers first, and set a reorder alert "
                      "at about two weeks of stock cover.",
            "priority_products": top,
            "why": "This is the main cause of the drop; demand per listed product held up.",
            "timeframe": "Within 48 to 72 hours",
            "expected_monthly_impact_inr": f["estimated_monthly_impact_inr"],
            "expected_impact_spoken": f"about {spoken_inr(f['estimated_monthly_impact_inr'])} a month",
        })
    if "cancellations" in by_id:
        f = by_id["cancellations"]
        recs.append({
            "id": "R2",
            "action": "Stop cancellations at the source: update stock counts daily, pause listings that hit "
                      "zero stock, and keep a small buffer on fast movers.",
            "why": f"{f['title']}, most likely from selling stock that was not there.",
            "timeframe": "This week",
            "expected_monthly_impact_inr": f["estimated_monthly_impact_inr"],
            "expected_impact_spoken": f"about {spoken_inr(f['estimated_monthly_impact_inr'])} a month "
                                      f"in delivered sales, and it protects your ranking",
        })
    if "visibility" in by_id:
        recs.append({
            "id": "R3",
            "action": "Once restocked, rebuild visibility: run a short promotion or sponsored listing on the "
                      "restocked best-sellers, and join an upcoming sale event if eligible.",
            "why": "Stock-outs and cancellations likely pushed listings down; a sales push helps ranking recover faster.",
            "timeframe": "Right after restocking",
            "expected_monthly_impact_inr": None,
            "expected_impact_spoken": "speeds up the recovery",
        })
    if "pricing" in by_id:
        recs.append({
            "id": "R-pricing",
            "action": "Review your prices against similar products and competitors.",
            "why": "Your average selling price changed.",
            "timeframe": "This week",
            "expected_monthly_impact_inr": None,
            "expected_impact_spoken": "could recover lost conversions",
        })
    return recs[:3]


def _headline(findings: list[dict[str, Any]], derived: dict[str, Any]) -> str:
    drop = f"Sales fell {abs(derived['sales_change_pct'])}% ({format_inr(derived['sales_lost'])})"
    if not findings:
        return drop + ". No clear cause in the available data."
    top = findings[0]
    return f"{drop}. Main cause: {top.get('cause', top['title'].lower())}."


if __name__ == "__main__":
    print(json.dumps(diagnose(load_seller_data()), indent=2, ensure_ascii=False))
