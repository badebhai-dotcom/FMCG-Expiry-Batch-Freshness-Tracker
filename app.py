"""FMCG Expiry & Batch Freshness Tracker - Streamlit dashboard. Run: streamlit run app.py"""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from src.allocation_engine import allocate_order, run_batch
from src.data_generator import load_or_generate
from src.metrics import (AMBER, BAND_COLORS, BAND_ORDER, CRITICAL, RED, compute_kpis,
                         distress_plan, enrich_inventory)

st.set_page_config(page_title="FMCG Freshness Tracker", page_icon="📦", layout="wide")
st.markdown("""<style>
div[data-testid="stMetric"]{background:#0F172A;border-radius:10px;padding:14px 18px;border-left:5px solid #38BDF8}
div[data-testid="stMetric"] *{color:#F8FAFC !important}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def get_data(version: int):
    return load_or_generate(force=version > 0)


@st.cache_data(show_spinner=False)
def get_batch(inv, rules, orders, dispatch):
    return run_batch(inv, rules, orders, dispatch)


def fmt_inr(x: float) -> str:
    return f"₹{x/1e5:,.2f} L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def paint(df: pd.DataFrame, col: str):
    def c(v):
        if str(v).startswith("Allocated"): return "background-color:#C8E6C9;color:#1B5E20"
        if str(v).startswith("Ineligible"): return "background-color:#FFCDD2;color:#B71C1C"
        return "background-color:#ECEFF1;color:#37474F"
    return df.style.map(c, subset=[col]) if hasattr(df.style, "map") else df.style.applymap(c, subset=[col])


def show_waterfall(lines: pd.DataFrame):
    v = lines[["batch_id", "exp_date", "residual_pct", "required_pct", "status", "allocated_cases"]].copy()
    v["exp_date"] = v["exp_date"].dt.strftime("%d-%b-%Y")
    v.columns = ["Batch ID", "Expiry Date", "Residual %", "Customer Requirement %", "Allocation Status", "Allocated Cases"]
    st.dataframe(paint(v, "Allocation Status"), use_container_width=True, hide_index=True)


# ------------------------------------------------------------------ sidebar
if "ver" not in st.session_state:
    st.session_state.ver = 0
st.sidebar.title("📦 Freshness Tracker")
dispatch = st.sidebar.date_input("Dispatch / as-of date", value=date.today())
if st.sidebar.button("♻️ Regenerate mock data (seed 42)"):
    st.session_state.ver += 1
    get_data.clear(); get_batch.clear()
inventory, rules, orders = get_data(st.session_state.ver)
enriched = enrich_inventory(inventory, dispatch)
b_lines, b_summary, b_inv_after = get_batch(inventory, rules, orders, pd.Timestamp(dispatch))
kpi = compute_kpis(enriched, b_summary)
plan = distress_plan(enriched, rules)

st.title("FMCG Expiry & Batch Freshness Tracker")
st.caption("FEFO dispatch allocation for Modern Trade + warehouse distress early-warning")
tab1, tab2, tab3 = st.tabs(["📊 Executive KPIs", "🚚 FEFO Allocation Engine", "🚨 Distress & Liquidation"])

# ------------------------------------------------------------------ tab 1
with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Warehouse Freshness Index", f"{kpi['freshness_index_pct']:.1f}%", help="Value-weighted average residual shelf life")
    c2.metric("Capital in Distress", fmt_inr(kpi["capital_at_risk_inr"]),
              f"{kpi['capital_at_risk_inr']/kpi['total_stock_value_inr']*100:.0f}% of stock value", delta_color="off")
    c3.metric("Pending Orders Fulfilled", f"{kpi['orders_fulfilled_pct']:.1f}%", help="Cases allocated / cases ordered")
    c4.metric("Stockout Risk (cases)", f"{kpi['stockout_risk_cases']:,}", f"{kpi['orders_at_risk']} orders short", delta_color="inverse")
    st.info(f"🛡️ Shelf-life gating protects **{fmt_inr(kpi['rejection_avoidance_value_inr'])}** of stock from rejection "
            f"(≈ **{fmt_inr(kpi['chargeback_avoided_inr'])}** in chargebacks avoided) versus naive FEFO.")
    l, r = st.columns(2)
    by_status = enriched.groupby("status", as_index=False).agg(cases=("available_cases", "sum"), value=("stock_value_inr", "sum"))
    fig = px.pie(by_status, names="status", values="value", hole=0.55, color="status", color_discrete_map=BAND_COLORS,
                 category_orders={"status": BAND_ORDER}, title="Inventory value by freshness status")
    l.plotly_chart(fig, use_container_width=True)
    by_cat = enriched.groupby(["category", "status"], as_index=False)["available_cases"].sum()
    fig2 = px.bar(by_cat, x="category", y="available_cases", color="status", color_discrete_map=BAND_COLORS,
                  category_orders={"status": BAND_ORDER}, title="Aging stock by category (cases)", barmode="stack")
    r.plotly_chart(fig2, use_container_width=True)
    st.subheader("Order-line outcomes (all pending orders)")
    st.dataframe(b_summary.drop(columns=["rejection_avoided_cases"]), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ tab 2
with tab2:
    mode = st.radio("Mode", ["Single order", "Batch run from orders.csv"], horizontal=True)
    if mode == "Single order":
        a, b, c = st.columns(3)
        cust = a.selectbox("Customer", rules["customer_name"])
        sku = b.selectbox("SKU", sorted(inventory["sku_id"].unique()))
        qty = c.number_input("Order quantity (cases)", 1, 2000, 150, step=10)
        lines, s, _ = allocate_order(inventory, rules, cust, sku, int(qty), pd.Timestamp(dispatch))
        m1, m2, m3 = st.columns(3)
        m1.metric("Required residual shelf life", f"{s['required_pct']:.0f}%")
        m2.metric("Allocated cases", f"{s['allocated_cases']:,} / {s['ordered_cases']:,}")
        m3.metric("Shortfall", f"{s['shortfall_cases']:,}")
        if s["shortfall_cases"] > 0:
            st.error(f"⚠️ {s['status']} - shortfall of {s['shortfall_cases']:,} cases.")
        else:
            st.success("✅ Order fully fulfilled under FEFO.")
        n_rej = int(lines["status"].str.startswith("Ineligible").sum())
        if n_rej:
            st.warning(f"🚫 {n_rej} batch(es) rejected for {cust}: residual shelf life below {s['required_pct']:.0f}%.")
        if lines.empty:
            st.info("No stock available for this SKU.")
        else:
            show_waterfall(lines)
    else:
        st.caption("Orders are processed strictest-freshness-customer first, against one shared stock pool.")
        sel = st.selectbox("Show waterfall for order", b_summary["order_id"])
        row = b_summary[b_summary["order_id"] == sel].iloc[0]
        if row["shortfall_cases"] > 0:
            st.error(f"{sel}: {row['status']} - short {row['shortfall_cases']} cases ({row['customer_name']}, {row['sku_id']}).")
        else:
            st.success(f"{sel}: fully fulfilled ({row['customer_name']}, {row['sku_id']}).")
        show_waterfall(b_lines[b_lines["order_id"] == sel])
        st.download_button("⬇️ Download allocation log (CSV)", b_lines.to_csv(index=False), "allocation_log.csv", "text/csv")

# ------------------------------------------------------------------ tab 3
with tab3:
    show = st.multiselect("Bands", [AMBER, RED, CRITICAL], default=[AMBER, RED, CRITICAL])
    view = plan[plan["status"].isin(show)]
    d1, d2, d3 = st.columns(3)
    d1.metric("Batches flagged", len(view))
    d2.metric("Value at risk", fmt_inr(view["loss_if_no_action_inr"].sum()))
    d3.metric("Expected recovery if actioned", fmt_inr(view["expected_recovery_inr"].sum()),
              help="Assumes 90% / 60% / 25% cost recovery for Amber / Red / Critical")
    t = view.copy()
    t["exp_date"] = t["exp_date"].dt.strftime("%d-%b-%Y")
    st.dataframe(t.drop(columns=["recommendation"]), use_container_width=True, hide_index=True)
    st.subheader("Recommended actions (top 10 by value)")
    for _, r in view.sort_values("loss_if_no_action_inr", ascending=False).head(10).iterrows():
        icon = {AMBER: "🟡", RED: "🔴", CRITICAL: "🟣"}[r["status"]]
        st.markdown(f"{icon} {r['recommendation']} · drops a band in **{r['days_to_next_band']}d**")
    st.download_button("⬇️ Export Distress Clearance Plan (CSV)", view.to_csv(index=False),
                       "distress_clearance_plan.csv", "text/csv")
