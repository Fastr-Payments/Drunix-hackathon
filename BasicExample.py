"""ONE basic example through every block.  Run: python3 examples/basic_example.py [--backend peer]
Story: IssuerB owns 1000 units of a 2-year 8% corporate bond (face 100). BankA buys it with wCBDC,
then pledges it as collateral for a loan and repays. Every chaincode call is traced as  [chain] Function(args)."""
import sys, time, argparse, numpy as np
sys.path.insert(0, ".")
from cse import crypto
from cse.ledgers import MockDrunix, MockWCBDC
from cse.drunix_adapter import DrunixClient, PeerCLITransport
from cse.engine import CSE, DCENode, sign
from pricing.models import LiquidityKalmanFilter, Vasicek, CorporateBond, risk_metrics, dynamic_ltv, dynamic_rate, RiskAwareSmartOrderRouter
from pricing.oracle import push_risk
from cse.reconcile import reconcile, fair_value_from_chain

ap = argparse.ArgumentParser(); ap.add_argument("--backend", default="mock", choices=["mock", "peer"]); args = ap.parse_args()
def title(s): print(f"\n{'='*78}\n{s}\n{'='*78}")
def say(s): print("  " + s)

class Traced:                                    # prints every ledger call so the video can follow along
    def __init__(s, o, tag): s._o, s._t = o, tag
    def __getattr__(s, n):
        a = getattr(s._o, n)
        if not callable(a) or n.startswith("_") or n in ("now",): return a
        def w(*x, **k):
            shown = ", ".join(str(v)[:22] for v in x if not isinstance(v, dict))
            if n not in ("get_token", "get_position"): print(f"      [{s._t}] {n}({shown})")
            return a(*x, **k)
        return w

# ---------------------------------------------------------------- STEP 0
title("STEP 0  Setup: identities, ledgers, chaincode bootstrap")
node_priv, node_pub = {}, {}
for n in "ABCD": node_priv[f"DCE-{n}"], node_pub[f"DCE-{n}"] = crypto.keypair()
w_priv, w_pub = {}, {}
for p in ("BankA", "IssuerB"): w_priv[p], w_pub[p] = crypto.keypair()
if args.backend == "mock": raw = MockDrunix(node_pub, threshold=3)
else:
    raw = DrunixClient(PeerCLITransport())                       # needs a running Drunix test-network + deployed bondcc
    for n, pk in node_pub.items(): raw.t.invoke("RegisterCSENode", n, pk)
    raw.t.invoke("SetThreshold", 3)
dx, cb = Traced(raw, "chain"), MockWCBDC()
for p in ("BankA", "IssuerB"): dx.set_kyc(p)
dx.list_asset("INE-DEMO-001", 7500); dx.mint("BOND-1", "INE-DEMO-001", "IssuerB", 1000)
cb.fund("BankA", 500_000); cb.fund("POOL", 1_000_000)
say("4 DCE nodes (Bank A, Issuer B, Market Operator, Regulator) each hold an ed25519 key; need 2f+1 = 3 to sign (f=1).")

# ---------------------------------------------------------------- STEP 1
title("STEP 1  LiquidityKalmanFilter: noisy yield quotes -> one clean starting rate r0")
kf = LiquidityKalmanFilter()
for q in [7.10, 7.14, 7.12]: say(f"quote {q:.2f}%  ->  filtered {kf.update(q):.4f}%")
r0 = kf.x / 100; say(f"r0 = {r0:.4%}   (gain k = P/(P+R): trusts new quotes less as certainty P shrinks)")

# ---------------------------------------------------------------- STEP 2
title("STEP 2  Vasicek model:  dr = a(b - r)dt + sigma dW")
vas = Vasicek(a=0.4, b=0.07, sigma=0.012)
say(f"a={vas.a} (pull speed)  b={vas.b:.0%} (long-run rate)  sigma={vas.s} (randomness per sqrt-year)")
dt = 0.25; e = np.exp(-vas.a * dt); mean = r0 * e + vas.b * (1 - e); sd = vas.s * np.sqrt((1 - e**2) / (2 * vas.a))
say(f"ONE step of dt=0.25y from r0={r0:.4%}:")
say(f"   mean = r0*e^(-a*dt) + b*(1-e^(-a*dt)) = {mean:.4%}   (drifts toward b)")
say(f"   sd   = sigma*sqrt((1-e^(-2a*dt))/(2a)) = {sd:.4%}")
for z in (-1, 0, 1): say(f"   shock Z={z:+d}  ->  r(0.25y) = {mean + sd*z:.4%}")
T = 2; n = 100_000; paths = vas.simulate(r0, T, 200, n, seed=3); dtp = T / 200
integral = ((paths[:, :-1] + paths[:, 1:]) / 2 * dtp).sum(axis=1)
say(f"CHECK  Monte Carlo {n:,} paths: mean(exp(-integral r dt)) = {np.exp(-integral).mean():.5f}   vs closed form P(0,2y) = {vas.zcb(np.array([r0]), 2.0)[0]:.5f}")

# ---------------------------------------------------------------- STEP 3
title("STEP 3  CorporateBond.price: discount each cashflow with Vasicek zero-coupon price x credit spread")
bond = CorporateBond(face=100, coupon=0.08, maturity=2, spread=0.015); tot = 0
for t_, cf in ((1, 8.0), (2, 108.0)):
    df = vas.zcb(np.array([r0]), t_)[0] * np.exp(-bond.spread * t_); tot += cf * df
    say(f"year {t_}: cashflow {cf:>6.1f} x discount {df:.5f} = {cf*df:7.3f}")
say(f"bond price = {tot:.3f}   (function says {bond.price(vas, r0)[0]:.3f})   -> 1000 units = INR {tot*1000:,.0f}")

# ---------------------------------------------------------------- STEP 4
title("STEP 4  risk_metrics + dynamic_ltv + oracle.push_risk  (writes integers to the chain)")
res = push_risk(dx, "INE-DEMO-001", bond, vas, r0, spread_vol=0.004)
say(f"10-day horizon: VaR99={res['VaR']:.2%}  ES99={res['ES']:.2%}  ->  LTV = 1-(10%+4xES) = {res['ltv_bps']/100:.1f}%")
say(f"on-chain fixed point: priceE6={res['price_e6']}  ltvBps={res['ltv_bps']}  (no floats in chaincode)")

# ---------------------------------------------------------------- STEP 5
title("STEP 5  CSE atomic DvP: BankA pays wCBDC, gets the bond")
cse = CSE(dx, cb, [DCENode(n, k, byzantine=(n == "DCE-D")) for n, k in node_priv.items()], w_pub, kyc={"BankA", "IssuerB"}, fair_value=fair_value_from_chain(raw), band=0.05)
price = int(round(tot * 1000))
c = dict(token_id="BOND-1", price=price, nonce=time.time(), expiry=time.time() + 300, atomicity="ALL_OR_NONE")
b = dict(side="BUY", party="BankA", **c); s = dict(side="SELL", party="IssuerB", **c)
b["sig"], s["sig"] = sign(w_priv["BankA"], b), sign(w_priv["IssuerB"], s)
say("Both signed intents enter. DCE-D is faulty and stays silent; 3 of 4 signatures is still enough.")
batch = cse.execute_dvp(b, s)
say(f"batch state: {batch['state'].value}   signers: {sorted(batch['proof']['sigs'])}")
say(f"owner now: {dx.get_token('BOND-1')['owner']}   cash: BankA={cb.bal['BankA']:,}  IssuerB={cb.bal['IssuerB']:,}")
for a in cse.audit: say(f"audit: {a['frm'].value:>15} -> {a['to'].value}   hash {a['hash'][:10]}")
say(f"audit chain verifies: {cse.verify_audit()}   reconciliation: {reconcile(cse, raw, cb)}")

# ---------------------------------------------------------------- STEP 6
title("STEP 6  Collateralisation: pledge the bond, borrow, repay, auto-release")
dx.lock_collateral("BOND-1")
loan = int(0.5 * tot * 1000 * res["ltv_bps"] / 10_000); say(f"borrow half the limit: INR {loan:,}")
venues = [dict(name="Venue_A", quote=7.12, variance=0.02, depth=40_000), dict(name="Venue_B", quote=7.15, variance=0.05, depth=100_000), dict(name="Venue_C", quote=7.13, variance=0.01, depth=30_000)]
say(f"RiskAwareSmartOrderRouter splits the funding ask: {RiskAwareSmartOrderRouter(venues).route_order(loan, kf.x)}")
dx.borrow("BOND-1", loan); cb.transfer("POOL", "BankA", loan)
rate = dynamic_rate(0.07, loan, loan * 2); say(f"borrow rate (utilisation 50%) = {rate:.2%}")
try: dx.release_collateral("BOND-1")
except Exception as ex: say(f"release while in debt is refused: {ex}")
cb.transfer("BankA", "POOL", loan); dx.repay("BOND-1", loan); dx.release_collateral("BOND-1")
say(f"after repay: status={dx.get_token('BOND-1')['status']}  position={dx.get_position('BOND-1')}")

title("DONE: Kalman -> Vasicek -> bond price -> ES/LTV -> chain oracle -> BFT DvP -> collateral loop, all on one bond")
