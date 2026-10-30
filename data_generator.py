"""
Synthetic FMCG dataset generator (reproducible, seed=42).

Produces three linked datasets:
  inventory_batches.csv  - warehouse stock by batch
  retailer_rules.csv     - channel acceptance rules (min residual shelf life)
  orders.csv             - pending outbound orders

Supply-chain note
-----------------
Shelf life is fixed per category (Dairy 60d, Biscuits 180d, Juices 120d,
Instant Noodles 270d, Personal Care 365d). A batch's age is drawn between
30 and 180 days per the brief. Because a 60-day dairy product cannot be 30-180
days old and still be sellable, ages are capped at (shelf_life - 1) day, and
short-life categories (<180d) use a 5-day lower bound so the demo has a
realistic mix of fresh and aging stock.
"""
from __future__ import annotations

import os
from datetime import date

import numpy as np
import pandas as pd

SEED = 42
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

FILES = {
    "inventory": "inventory_batches.csv",
    "retailers": "retailer_rules.csv",
    "orders": "orders.csv",
}

# category -> total shelf life (days)
SHELF_LIFE = {
    "Dairy": 60,
    "Juices": 120,
    "Biscuits": 180,
    "Instant Noodles": 270,
    "Personal Care": 365,
}

# (sku_id, category, base cost per case in INR)
SKU_MASTER = [
    ("SKU-101", "Biscuits", 520), ("SKU-102", "Biscuits", 740), ("SKU-103", "Biscuits", 910),
    ("SKU-104", "Dairy", 880), ("SKU-105", "Dairy", 1250), ("SKU-106", "Dairy", 1780),
    ("SKU-107", "Personal Care", 1900), ("SKU-108", "Personal Care", 2600), ("SKU-109", "Personal Care", 3400),
    ("SKU-110", "Instant Noodles", 430), ("SKU-111", "Instant Noodles", 560), ("SKU-112", "Instant Noodles", 690),
    ("SKU-113", "Juices", 620), ("SKU-114", "Juices", 810), ("SKU-115", "Juices", 1040),
]

RETAILER_RULES = [
    # customer, channel, min residual shelf life, chargeback per rejected case (INR)
    ("DMart", "Modern Trade", 0.75, 180.0),
    ("Reliance Retail", "Modern Trade", 0.70, 150.0),
    ("Blinkit DarkStore", "Quick Commerce", 0.60, 120.0),
    ("Zepto DarkStore", "Quick Commerce", 0.60, 120.0),
    ("General Trade / Local Distributor", "General Trade", 0.40, 50.0),
]


def generate_datasets(sim_date: date | None = None, seed: int = SEED):
    """Return (inventory, retailers, orders) DataFrames."""
    rng = np.random.default_rng(seed)
    sim = pd.Timestamp(sim_date or date.today()).normalize()

    # ---- A. Warehouse batches ------------------------------------------------
    rows, n = [], 1001
    for sku, cat, base_cost in SKU_MASTER:
        shelf = SHELF_LIFE[cat]
        lo = 30 if shelf >= 180 else 5
        hi = min(180, shelf - 1)
        for age in sorted(rng.integers(lo, hi + 1, size=int(rng.integers(2, 5)))):
            mfg = sim - pd.Timedelta(days=int(age))
            exp = mfg + pd.Timedelta(days=shelf)
            cost = float(np.clip(base_cost * rng.uniform(0.95, 1.08), 400, 3500))
            rows.append({
                "batch_id": f"BAT-{n}", "sku_id": sku, "category": cat,
                "mfg_date": mfg, "exp_date": exp,
                "total_shelf_life_days": (exp - mfg).days,
                "available_cases": int(rng.integers(40, 801)),
                "unit_cost_inr": round(cost, 2),
                "warehouse_location": f"Dock-{rng.choice(list('ABCD'))}-Rack-{int(rng.integers(1, 21)):02d}",
            })
            n += 1
    inventory = pd.DataFrame(rows)

    # ---- B. Retailer master --------------------------------------------------
    retailers = pd.DataFrame(RETAILER_RULES, columns=[
        "customer_name", "channel_type", "min_shelf_life_pct_required", "penalty_per_rejected_case_inr"])

    # ---- C. Pending orders ---------------------------------------------------
    custs = retailers["customer_name"].tolist()
    skus = [s[0] for s in SKU_MASTER]
    orders = pd.DataFrame({
        "order_id": [f"ORD-{5001 + i}" for i in range(30)],
        "customer_name": rng.choice(custs, size=30, p=[0.25, 0.25, 0.2, 0.15, 0.15]),
        "sku_id": rng.choice(skus, size=30),
        "ordered_cases": rng.integers(10, 301, size=30),
        "order_date": sim,
    })
    return inventory, retailers, orders


def save_datasets(inventory, retailers, orders, data_dir: str = DATA_DIR) -> None:
    os.makedirs(data_dir, exist_ok=True)
    inventory.to_csv(os.path.join(data_dir, FILES["inventory"]), index=False)
    retailers.to_csv(os.path.join(data_dir, FILES["retailers"]), index=False)
    orders.to_csv(os.path.join(data_dir, FILES["orders"]), index=False)


def load_or_generate(data_dir: str = DATA_DIR, force: bool = False):
    """Load CSVs; auto-generate them on first run (or when force=True)."""
    paths = {k: os.path.join(data_dir, v) for k, v in FILES.items()}
    if force or not all(os.path.exists(p) for p in paths.values()):
        save_datasets(*generate_datasets(), data_dir=data_dir)
    inv = pd.read_csv(paths["inventory"], parse_dates=["mfg_date", "exp_date"])
    ret = pd.read_csv(paths["retailers"])
    orders = pd.read_csv(paths["orders"], parse_dates=["order_date"])
    return inv, ret, orders


if __name__ == "__main__":
    save_datasets(*generate_datasets())
    print(f"Datasets written to {DATA_DIR}")
