"""In-process stand-ins for the settlement domains. MockDrunix is a line-for-line Python twin of
chaincode/bondcc (same function names/semantics, integer maths only) so everything runs without a network.
Swap in cse.drunix_adapter.DrunixClient to talk to the real chaincode on a Drunix network."""
import hashlib, json, time
from . import crypto

def h(obj) -> str: return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()
class LedgerError(Exception): pass

MAX_STALE = 86400          # seconds a risk update stays valid
LIQ_BUFFER_BPS = 500       # liquidate when debt > maxBorrow * 1.05

class MockDrunix:
    def __init__(self, node_pubkeys, threshold, now=time.time):
        self.node_pubkeys, self.threshold, self.now = node_pubkeys, threshold, now
        self.tokens, self.assets, self.positions, self.kyc, self.events = {}, {}, {}, set(), []
        self.fail_lock = False                                         # fault injection (demo only)

    def _emit(self, ev, **kw): self.events.append(dict(event=ev, ts=self.now(), **kw))
    def _tok(self, tid):
        if tid not in self.tokens: raise LedgerError(f"token {tid} not found")
        return self.tokens[tid]

    # ---- Compliance / registry ----
    def set_kyc(self, party, ok=True): (self.kyc.add if ok else self.kyc.discard)(party)
    def list_asset(self, isin, ltv_bps):
        if ltv_bps > 9000: raise LedgerError("ltv>90%")
        self.assets[isin] = dict(ltv_bps=ltv_bps, price_e6=0, updated=0)
    def update_risk(self, isin, ltv_bps, price_e6):
        if isin not in self.assets or ltv_bps > 9000: raise LedgerError("bad risk update")
        self.assets[isin].update(ltv_bps=ltv_bps, price_e6=price_e6, updated=int(self.now())); self._emit("RISK_UPDATED", isin=isin, ltv_bps=ltv_bps)
    def mint(self, token_id, isin, owner, units):
        if token_id in self.tokens: raise LedgerError("exists")
        self.tokens[token_id] = dict(id=token_id, isin=isin, owner=owner, units=units, status="FREE", batch="", expiry=0)
    def get_token(self, tid): return self.tokens.get(tid)

    # ---- DvP legs ----
    def lock_asset(self, batch_id, token_id, expiry):
        t = self._tok(token_id)
        if self.fail_lock or t["status"] != "FREE": raise LedgerError("lock failed")
        t.update(status="LOCKED", batch=batch_id, expiry=int(expiry)); self._emit("LOCKED", batch=batch_id, token=token_id)
    def release_lock(self, batch_id, token_id):
        t = self._tok(token_id)
        if t["status"] == "LOCKED" and t["batch"] == batch_id:
            t.update(status="FREE", batch="", expiry=0); self._emit("RELEASED", batch=batch_id, token=token_id)
    def _verify(self, proof):                                          # CSEVerifier
        msg = proof["commitment"].encode()
        good = sum(1 for n, s in proof["sigs"].items() if n in self.node_pubkeys and crypto.verify(self.node_pubkeys[n], msg, s))
        return good >= self.threshold
    def settle_leg(self, batch_id, token_id, new_owner, proof):
        t = self._tok(token_id)
        if t.get("settled") == batch_id: return                        # idempotent retry
        if t["status"] != "LOCKED" or t["batch"] != batch_id: raise LedgerError("not locked for batch")
        if int(self.now()) >= t["expiry"]: raise LedgerError("lock expired")
        if new_owner not in self.kyc: raise LedgerError("receiver KYC")
        if not self._verify(proof): raise LedgerError("bad proof")
        t.update(owner=new_owner, status="FREE", batch="", expiry=0, settled=batch_id)
        self._emit("TRANSFERRED", batch=batch_id, token=token_id, to=new_owner, proof=proof["commitment"])

    # ---- Collateral pool ----
    def max_borrow(self, tid):
        t = self._tok(tid); a = self.assets.get(t["isin"])
        if not a or int(self.now()) - a["updated"] > MAX_STALE: raise LedgerError("stale/no price")
        return t["units"] * a["price_e6"] // 1_000_000 * a["ltv_bps"] // 10_000
    def lock_collateral(self, tid):
        t = self._tok(tid)
        if t["status"] != "FREE" or t["isin"] not in self.assets: raise LedgerError("ineligible")
        t["status"] = "COLLATERAL"; self.positions[tid] = dict(debt=0); self._emit("COLLATERAL_LOCKED", token=tid)
    def borrow(self, tid, amount):
        t, p = self._tok(tid), self.positions.get(tid)
        if t["status"] != "COLLATERAL" or p is None: raise LedgerError("no collateral")
        if p["debt"] + amount > self.max_borrow(tid): raise LedgerError("LTV exceeded")
        p["debt"] += amount; self._emit("BORROWED", token=tid, amount=amount)
    def repay(self, tid, amount):
        p = self.positions[tid]; pay = min(amount, p["debt"]); p["debt"] -= pay; self._emit("REPAID", token=tid, amount=pay); return pay
    def release_collateral(self, tid):
        t, p = self._tok(tid), self.positions.get(tid)
        if t["status"] != "COLLATERAL" or p["debt"] != 0: raise LedgerError("debt outstanding")
        t["status"] = "FREE"; del self.positions[tid]; self._emit("COLLATERAL_RELEASED", token=tid)
    def liquidate(self, tid):
        t, p = self._tok(tid), self.positions[tid]
        if p["debt"] * 10_000 <= self.max_borrow(tid) * (10_000 + LIQ_BUFFER_BPS): raise LedgerError("healthy")
        t.update(owner="LIQUIDATOR", status="FREE"); p["debt"] = 0; self._emit("LIQUIDATED", token=tid)
    def get_position(self, tid): return self.positions.get(tid)

class MockWCBDC:
    """Cash leg: earmark -> debit/release, plus plain transfer for loan disbursal/repayment."""
    def __init__(self): self.bal, self.earmarks, self.receipts, self.fail_earmark = {}, {}, {}, False
    def fund(self, who, amt): self.bal[who] = self.bal.get(who, 0) + amt
    def transfer(self, a, b, amt):
        if self.bal.get(a, 0) < amt: raise LedgerError("insufficient")
        self.bal[a] -= amt; self.bal[b] = self.bal.get(b, 0) + amt
    def earmark(self, batch_id, payer, amt):
        if self.fail_earmark or self.bal.get(payer, 0) < amt: raise LedgerError("earmark failed")
        self.bal[payer] -= amt; self.earmarks[batch_id] = (payer, amt)
    def release_earmark(self, batch_id):
        if batch_id in self.earmarks: p, a = self.earmarks.pop(batch_id); self.bal[p] += a
    def debit(self, batch_id, payee, proof):
        if batch_id in self.receipts: return self.receipts[batch_id]
        if batch_id not in self.earmarks: raise LedgerError("no earmark")
        payer, amt = self.earmarks.pop(batch_id); self.bal[payee] = self.bal.get(payee, 0) + amt
        self.receipts[batch_id] = dict(batch=batch_id, payer=payer, payee=payee, amt=amt, proof=proof["commitment"]); return self.receipts[batch_id]
