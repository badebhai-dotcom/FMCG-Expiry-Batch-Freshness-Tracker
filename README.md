
## Requirements

- Python 3.10+
- streamlit, pandas, numpy, plotly

## Installation & Run

```bash
cd fmcg_tracker
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The dashboard opens at http://localhost:8501.

If the CSV files are missing, mock data is generated automatically, so
no setup is needed.

## Data Files (in `data/`)

| File | Contents |
|------|----------|
| `inventory_batches.csv` | Batches: SKU, category, mfg/exp dates, cases, unit cost, location |
| `retailer_rules.csv` | Customers, channel type, min shelf-life %, penalty per rejected case |
| `orders.csv` | Pending outbound orders (10 to 300 cases each) |

To use your own data, replace these files and keep the same column names.
Click "Regenerate mock data" in the sidebar to reset to the sample data.

## Core Logic

**Residual shelf life**

    Residual % = (exp_date - dispatch_date) / total_shelf_life_days * 100

**Allocation (per order line)**
1. Take batches for the SKU with available stock.
2. Keep only batches where Residual % >= the customer's minimum.
3. Sort eligible batches by earliest expiry (FEFO) and deduct until filled.
4. Batches below the threshold are marked `Ineligible - Shelf Life Breach`.
5. Any shortfall is flagged `Partial Fulfillment / Modern Trade OOS Risk`.

In batch runs, customers with the strictest freshness requirement are
served first, because fresh stock is scarce and only they can accept it.

**Freshness bands**

| Band | Residual shelf life | Action |
|------|--------------------|--------|
| Green (Fresh) | > 65% | Safe for Modern Trade and Quick Commerce |
| Amber (At Risk) | 40% to 65% | Route to General Trade / wholesale |
| Red (Distressed) | 15% to 40% | Clearance / promotion, RTV risk |
| Critical (Near Expiry) | < 15% | Lock from picking; liquidate or write off |

**KPIs**
- **Warehouse Freshness Index**: value-weighted average residual shelf life.
- **Capital in Distress**: stock value in Amber, Red and Critical bands.
- **Rejection Avoidance**: cost value of stock that plain FEFO would have
  shipped to a customer who would reject it, plus the chargebacks avoided.
- **Stockout Risk**: total unfulfilled cases across pending orders.

## Dashboard Tabs

1. **Executive KPIs**: metric cards, status donut chart, aging-by-category bar chart.
2. **FEFO Allocation Engine**: single-order or batch mode with a batch-by-batch
   allocation table, warnings, and CSV download of the allocation log.
3. **Distress & Liquidation**: Amber/Red/Critical batches with recommended
   channels and actions, plus CSV export of the Distress Clearance Plan.

Use the sidebar date to change the dispatch / as-of date.

## Assumptions

- Shelf life: Dairy 60d, Juices 120d, Biscuits 180d, Instant Noodles 270d,
  Personal Care 365d.
- Batch age is 30 to 180 days, capped below the shelf life. Categories under
  180 days use a 5-day minimum.
- Expected recovery rates in the clearance plan are 90% (Amber), 60% (Red)
  and 25% (Critical) of cost. Edit `RECOVERY_RATE` in `src/metrics.py`.

## Troubleshooting

- **`streamlit` not recognized**: use `python -m streamlit run app.py`
  (or `py -m streamlit run app.py` on Windows).
- **No module named ...**: re-run `python -m pip install -r requirements.txt`.
- **Port already in use**: `python -m streamlit run app.py --server.port 8502`
- 
