"""
FEFO dispatch allocation with customer residual-shelf-life constraints.

Core maths
----------
    Residual Shelf Life % = (exp_date - dispatch_date) / total_shelf_life_days * 100

A batch is eligible for a customer only if Residual % >= the customer's
min_shelf_life_pct_required. Eligible batches are consumed First-Expired-
First-Out so the oldest *acceptable* stock leaves the warehouse first, which
maximises sell-through before stock decays into a lower freshness band.
"""
from __future__ import annotations

import pandas as pd

PARTIAL_LABEL = "Partial Fulfillment / Modern Trade OOS Risk"
NO_STOCK_LABEL = "Unfulfilled - No Eligible Stock / Modern Trade OOS Risk"
FULL_LABEL = "Fulfilled"

LINE_COLUMNS = [
    "order_id", "customer_name", "batch_id", "sku_id", "exp_date", "residual_pct",
    "required_pct", "available_cases", "allocated_cases", "status", "reason",
    "unit_cost_inr", "warehouse_location",
]


def residual_shelf_life_pct(exp_date, dispatch_date, total_days):
    """Residual shelf life in % at dispatch (works on scalars or Series)."""
    return (exp_date - pd.Timestamp(dispatch_date)).dt.days / total_days * 100 \
        if isinstance(exp_date, pd.Series) else \
        (pd.Timestamp(exp_date) - pd.Timestamp(dispatch_date)).days / total_days * 100


def allocate_order(inventory: pd.DataFrame, rules: pd.DataFrame, customer: str, sku_id: str,
                   qty: int, dispatch_date, order_id: str = "ADHOC"):
    """
    Allocate one order line. Returns (waterfall_df, summary_dict, inventory_after).
    The input inventory is never mutated; inventory_after carries the deductions.
    """
    inv = inventory.copy()
    rule = rules.loc[rules["customer_name"] == customer].iloc[0]
    required_pct = float(rule["min_shelf_life_pct_required"]) * 100

    cand = inv[(inv["sku_id"] == sku_id) & (inv["available_cases"] > 0)].copy()
    cand["residual_pct"] = residual_shelf_life_pct(cand["exp_date"], dispatch_date, cand["total_shelf_life_days"])
    days_left = (cand["exp_date"] - pd.Timestamp(dispatch_date)).dt.days
    cand["eligible"] = (cand["residual_pct"] >= required_pct - 1e-9) & (days_left > 0)

    # Strict FEFO among eligible batches; ineligible ones are listed after for audit.
    eligible = cand[cand["eligible"]].sort_values(["exp_date", "batch_id"])
    rejected = cand[~cand["eligible"]].sort_values(["exp_date", "batch_id"])

    remaining, lines = int(qty), []

    def _line(r, take, status, reason):
        lines.append({
            "order_id": order_id, "customer_name": customer, "batch_id": r["batch_id"],
            "sku_id": r["sku_id"], "exp_date": r["exp_date"], "residual_pct": round(r["residual_pct"], 1),
            "required_pct": round(required_pct, 1), "available_cases": int(r["available_cases"]),
            "allocated_cases": int(take), "status": status, "reason": reason,
            "unit_cost_inr": r["unit_cost_inr"], "warehouse_location": r["warehouse_location"],
        })

    for idx, r in eligible.iterrows():
        take = min(remaining, int(r["available_cases"]))
        if take > 0:
            inv.loc[idx, "available_cases"] -= take
            remaining -= take
            _line(r, take, "Allocated", f"FEFO pick; residual {r['residual_pct']:.1f}% >= {required_pct:.0f}%")
        else:
            _line(r, 0, "Eligible - Not Needed", "Order already fulfilled by earlier-expiry batches")

    for _, r in rejected.iterrows():
        expired = r["residual_pct"] <= 0
        _line(r, 0, "Ineligible - Expired" if expired else "Ineligible - Shelf Life Breach",
              f"Residual {r['residual_pct']:.1f}% < required {required_pct:.0f}%" if not expired else "Past expiry date")

    allocated = int(qty) - remaining

    # Counterfactual: naive FEFO ignoring the threshold would have shipped some
    # of the breaching batches and triggered a chargeback/rejection at the dock.
    naive_left, avoided_cases, avoided_value = int(qty), 0, 0.0
    for _, r in cand.sort_values(["exp_date", "batch_id"]).iterrows():
        take = min(naive_left, int(r["available_cases"]))
        naive_left -= take
        if not r["eligible"]:
            avoided_cases += take
            avoided_value += take * r["unit_cost_inr"]
        if naive_left == 0:
            break

    status = FULL_LABEL if remaining == 0 else (PARTIAL_LABEL if allocated > 0 else NO_STOCK_LABEL)
    summary = {
        "order_id": order_id, "customer_name": customer, "sku_id": sku_id,
        "ordered_cases": int(qty), "allocated_cases": allocated, "shortfall_cases": remaining,
        "fulfillment_pct": round(allocated / qty * 100, 1) if qty else 100.0,
        "required_pct": round(required_pct, 1), "status": status,
        "rejection_avoided_cases": int(avoided_cases),
        "rejection_avoided_value_inr": round(avoided_value, 2),
        "chargeback_avoided_inr": round(avoided_cases * float(rule["penalty_per_rejected_case_inr"]), 2),
    }
    return pd.DataFrame(lines, columns=LINE_COLUMNS), summary, inv


def run_batch(inventory: pd.DataFrame, rules: pd.DataFrame, orders: pd.DataFrame, dispatch_date):
    """
    Allocate all pending orders against one shared inventory pool.

    Priority rule: customers with the STRICTEST freshness requirement are served
    first. Fresh stock is scarce and only they can take it; if a lenient
    customer (e.g. General Trade) consumed it first, stricter orders would OOS
    while older stock sat idle.
    """
    thr = rules.set_index("customer_name")["min_shelf_life_pct_required"]
    ordered = orders.assign(_thr=orders["customer_name"].map(thr)) \
                    .sort_values(["_thr", "order_id"], ascending=[False, True])
    inv, all_lines, summaries = inventory.copy(), [], []
    for _, o in ordered.iterrows():
        lines, summ, inv = allocate_order(inv, rules, o["customer_name"], o["sku_id"],
                                          int(o["ordered_cases"]), dispatch_date, o["order_id"])
        all_lines.append(lines)
        summaries.append(summ)
    lines_df = pd.concat(all_lines, ignore_index=True) if all_lines else pd.DataFrame(columns=LINE_COLUMNS)
    return lines_df, pd.DataFrame(summaries), inv
