"""Reconciliation: three records per trade (Drunix tx, wCBDC receipt, CSE/marketplace batch) must collapse to one batchId + proof ref."""
from .engine import S

def fair_value_from_chain(dx):
    """Returns fair_value(token_id) using the oracle price stored on-chain (links pricing engine -> CSE validation)."""
    def fv(token_id):
        t = dx.get_token(token_id); a = dx.get_asset(t["isin"]) if t else None
        return t["units"] * a["price_e6"] / 1e6 if a and a["price_e6"] else None
    return fv

def reconcile(cse, dx, cb):
    settled = [b for b in cse.batches.values() if b["state"] == S.SETTLED]
    transferred = {e["batch"] for e in dx.events if e["event"] == "TRANSFERRED"}
    matched = [b for b in settled if b["id"] in transferred and b["id"] in cb.receipts
               and cb.receipts[b["id"]]["proof"] == b["proof"]["commitment"]]
    orphans = sorted((transferred | set(cb.receipts)) - {b["id"] for b in settled})
    return dict(settled=len(settled), matched=len(matched), rate=(len(matched) / len(settled) if settled else 1.0), orphans=orphans)
