"""
Freshness classification, financial-risk KPIs and the distress clearance plan.

Bands (residual shelf life at the as-of date):
  Green    > 65%      safe for Modern Trade & Quick Commerce
  Amber    40-65%     too short for top-tier MT -> route to General Trade
  Red      15-40%     clearance / promotion now; high RTV risk
  Critical < 15%      lock from picking; liquidate / write off
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GREEN, AMBER, RED, CRITICAL = "Green (Fresh)", "Amber (At Risk)", "Red (Distressed)", "Critical (Near Expiry)"
BAND_ORDER = [GREEN, AMBER, RED, CRITICAL]
BAND_COLORS = {GREEN: "#2E7D32", AMBER: "#F9A825", RED: "#D32F2F", CRITICAL: "#4A148C"}
# Lower edge of each band as a fraction of total shelf life (used to forecast band migration)
BAND_FLOOR = {GREEN: 0.65, AMBER: 0.40, RED: 0.15, CRITICAL: 0.0}

# Planning assumptions: share of cost recovered under each action (editable)
RECOVERY_RATE = {AMBER: 0.90, RED: 0.60, CRITICAL: 0.25}


def enrich_inventory(inventory: pd.DataFrame, as_of) -> pd.DataFrame:
    """Add days_to_expiry, residual %, freshness band, stock value and days until next band drop."""
    as_of = pd.Timestamp(as_of).normalize()
    df = inventory.copy()
    df["days_to_expiry"] = (df["exp_date"] - as_of).dt.days
    df["residual_pct"] = df["days_to_expiry"] / df["total_shelf_life_days"] * 100
    df["status"] = np.select(
        [df["residual_pct"] > 65, df["residual_pct"] >= 40, df["residual_pct"] >= 15],
        [GREEN, AMBER, RED], default=CRITICAL)
    df["stock_value_inr"] = df["available_cases"] * df["unit_cost_inr"]
    floor = df["status"].map(BAND_FLOOR)
    df["days_to_next_band"] = (df["days_to_expiry"] - floor * df["total_shelf_life_days"]).round().clip(lower=0).astype(int)
    df["pickable"] = df["status"] != CRITICAL  # Critical stock is locked from picking
    return df


def freshness_index(enriched: pd.DataFrame) -> float:
    """Value-weighted mean residual shelf life (%): expensive stock counts more than cheap stock."""
    if enriched.empty or enriched["stock_value_inr"].sum() == 0:
        return 0.0
    return float(np.average(enriched["residual_pct"].clip(0, 100), weights=enriched["stock_value_inr"]))


def compute_kpis(enriched: pd.DataFrame, order_summary: pd.DataFrame | None = None) -> dict:
    at_risk = enriched[enriched["status"] != GREEN]
    k = {
        "freshness_index_pct": freshness_index(enriched),
        "capital_at_risk_inr": float(at_risk["stock_value_inr"].sum()),
        "total_stock_value_inr": float(enriched["stock_value_inr"].sum()),
        "orders_fulfilled_pct": 0.0, "stockout_risk_cases": 0,
        "rejection_avoidance_value_inr": 0.0, "chargeback_avoided_inr": 0.0, "orders_at_risk": 0,
    }
    if order_summary is not None and not order_summary.empty:
        k["orders_fulfilled_pct"] = float(order_summary["allocated_cases"].sum() / order_summary["ordered_cases"].sum() * 100)
        k["stockout_risk_cases"] = int(order_summary["shortfall_cases"].sum())
        k["rejection_avoidance_value_inr"] = float(order_summary["rejection_avoided_value_inr"].sum())
        k["chargeback_avoided_inr"] = float(order_summary["chargeback_avoided_inr"].sum())
        k["orders_at_risk"] = int((order_summary["shortfall_cases"] > 0).sum())
    return k


def distress_plan(enriched: pd.DataFrame, rules: pd.DataFrame) -> pd.DataFrame:
    """One row per Amber/Red/Critical batch with a recommended channel and action."""
    d = enriched[enriched["status"] != GREEN].copy()

    def qualifying(res):
        ok = rules.loc[rules["min_shelf_life_pct_required"] * 100 <= res, "customer_name"].tolist()
        return ", ".join(ok) if ok else "None"

    def action(r):
        v = f"INR {r['stock_value_inr']:,.0f}"
        if r["status"] == AMBER:
            return ("General Trade / Wholesale",
                    f"Re-route Batch {r['batch_id']} to General Trade to prevent {v} write-off")
        if r["status"] == RED:
            return ("Clearance Promo / Wholesale",
                    f"Run clearance promotion on Batch {r['batch_id']} ({r['days_to_expiry']}d left) to avoid RTV of {v}")
        return ("Liquidation / Write-off",
                f"Lock Batch {r['batch_id']} from picking; liquidate or write off {v}")

    d["eligible_customers"] = d["residual_pct"].apply(qualifying)
    d[["recommended_channel", "recommendation"]] = d.apply(lambda r: pd.Series(action(r)), axis=1)
    d["expected_recovery_inr"] = (d["stock_value_inr"] * d["status"].map(RECOVERY_RATE)).round(0)
    d["loss_if_no_action_inr"] = d["stock_value_inr"]
    d["residual_pct"] = d["residual_pct"].round(1)
    cols = ["batch_id", "sku_id", "category", "warehouse_location", "exp_date", "days_to_expiry", "residual_pct",
            "status", "available_cases", "stock_value_inr", "days_to_next_band", "eligible_customers",
            "recommended_channel", "recommendation", "expected_recovery_inr", "loss_if_no_action_inr"]
    # Most urgent first: worst band, then least time left
    d["_rank"] = d["status"].map({CRITICAL: 0, RED: 1, AMBER: 2})
    return d.sort_values(["_rank", "days_to_expiry"])[cols].reset_index(drop=True)
