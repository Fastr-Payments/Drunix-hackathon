"""Pricing engine <-> chain, live.  Market stress: yields and spreads widen, the oracle re-prices, the on-chain
LTV/price fall, a loan that was safe becomes liquidatable.  Run: python3 examples/oracle_liquidation_demo.py"""
import sys; sys.path.insert(0, ".")
from cse import crypto
from cse.ledgers import MockDrunix, LedgerError
from pricing.models import LiquidityKalmanFilter, Vasicek, CorporateBond
from pricing.oracle import push_risk

dx = MockDrunix({}, threshold=1); dx.list_asset("INE-DEMO-001", 7500); dx.mint("BOND-1", "INE-DEMO-001", "BankA", 1000)
vas, kf = Vasicek(), LiquidityKalmanFilter()
def tick(label, quotes, spread, spread_vol):
    for q in quotes: kf.update(q)
    r = push_risk(dx, "INE-DEMO-001", CorporateBond(coupon=0.08, maturity=2, spread=spread), vas, kf.x / 100, spread_vol=spread_vol)
    mb, debt = dx.max_borrow("BOND-1"), (dx.get_position("BOND-1") or {}).get("debt", 0)
    hf = (mb / debt) if debt else float("inf")
    print(f"{label:<10} filtered yield {kf.x:5.2f}%  spread {spread:.1%}  price {r['price']:6.2f}  ES99 {r['ES']:5.2%}  "
          f"LTV {r['ltv_bps']/100:4.1f}%  maxBorrow {mb:>7,}  debt {debt:>7,}  health {hf:4.2f}")
    return debt

print("--- calm market ---")
tick("t0 calm", [7.10, 7.12, 7.11], 0.015, 0.004)
dx.lock_collateral("BOND-1"); dx.borrow("BOND-1", 80_000); print("BankA pledges the bond and borrows 80,000\n--- stress builds: oracle re-prices every tick ---")
for lbl, qs, sp, sv in [("t1", [7.8, 8.2], 0.025, 0.006), ("t2", [8.8, 9.2, 9.5], 0.040, 0.010), ("t3", [9.8, 10.2, 10.5], 0.060, 0.015)]:
    tick(lbl, qs, sp, sv)
    try: dx.liquidate("BOND-1"); print("           >>> LIQUIDATED on-chain (debt > maxBorrow x 1.05)  owner ->", dx.get_token("BOND-1")["owner"]); break
    except LedgerError as e: print(f"           liquidate refused: {e}")
