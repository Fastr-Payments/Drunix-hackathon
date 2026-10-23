"""Coordination & Settlement Engine: intent -> validate -> BFT batch -> reserve legs -> commit -> settle."""
import hashlib, json, time, uuid
from . import crypto
from enum import Enum
from .ledgers import MockDrunix, MockWCBDC, LedgerError, h

class S(str, Enum):
    INTENT_RECEIVED="INTENT_RECEIVED"; VALIDATED="VALIDATED"; REJECTED="REJECTED"; BATCHED="BATCHED"
    LEGS_RESERVED="LEGS_RESERVED"; COMMIT_DECIDED="COMMIT_DECIDED"; SETTLED="SETTLED"
    ABORTING="ABORTING"; RELEASED="RELEASED"

LEGAL = {S.INTENT_RECEIVED:{S.VALIDATED,S.REJECTED}, S.VALIDATED:{S.BATCHED,S.REJECTED},
         S.BATCHED:{S.LEGS_RESERVED,S.ABORTING}, S.LEGS_RESERVED:{S.COMMIT_DECIDED,S.ABORTING},
         S.COMMIT_DECIDED:{S.SETTLED}, S.ABORTING:{S.RELEASED}}

def sign(priv, intent: dict) -> str:
    return crypto.sign(priv, crypto.canon(intent))

def merkle_root(hashes):
    lvl = hashes[:] or [""]
    while len(lvl) > 1:
        if len(lvl) % 2: lvl.append(lvl[-1])
        lvl = [hashlib.sha256((a+b).encode()).hexdigest() for a, b in zip(lvl[::2], lvl[1::2])]
    return lvl[0]

class DCENode:
    """One institution's node. Independently re-validates and signs the batch commitment."""
    def __init__(self, name, priv_key, byzantine=False): self.name, self.key, self.byzantine = name, priv_key, byzantine
    def vote(self, commitment, valid: bool):
        if self.byzantine or not valid: return None
        return crypto.sign(self.key, commitment.encode())

class CSE:
    def __init__(self, drunix, wcbdc, nodes, wallet_keys, kyc, lock_ttl=30, clock=time.time):
        self.dx, self.cb, self.nodes, self.wallets, self.kyc = drunix, wcbdc, nodes, wallet_keys, kyc
        self.f = (len(nodes) - 1) // 3; self.quorum = 2 * self.f + 1
        self.ttl, self.clock = lock_ttl, clock
        self.audit, self.batches = [], {}

    def _go(self, b, new):
        assert new in LEGAL[b["state"]], f"illegal {b['state']}->{new}"
        self.audit.append(dict(batch=b["id"], frm=b["state"], to=new, ts=self.clock()))
        b["state"] = new

    def _validate(self, intents):
        buy = next(i for i in intents if i["side"] == "BUY"); sell = next(i for i in intents if i["side"] == "SELL")
        for i in (buy, sell):
            if i["party"] not in self.wallets or not crypto.verify(self.wallets[i["party"]], crypto.canon(i), i["sig"]):
                return False, "bad signature"
            if i["party"] not in self.kyc: return False, "KYC fail"
        if (buy["token_id"], buy["price"]) != (sell["token_id"], sell["price"]): return False, "intent mismatch"
        tok = self.dx.get_token(sell["token_id"])
        if not tok or tok["owner"] != sell["party"]: return False, "seller does not own asset"
        if self.cb.bal.get(buy["party"], 0) < buy["price"]: return False, "insufficient funds"
        return True, "ok"

    def execute_dvp(self, buy, sell, max_retries=5):
        b = dict(id="B-" + uuid.uuid4().hex[:8], state=S.INTENT_RECEIVED, intents=[buy, sell], proof=None)
        self.batches[b["id"]] = b
        ok, why = self._validate([buy, sell])
        if not ok: self._go(b, S.REJECTED); b["reason"] = why; return b
        self._go(b, S.VALIDATED)

        commitment = merkle_root(sorted(h(i) for i in b["intents"])) + ":" + b["id"]
        sigs = {n.name: s for n in self.nodes if (s := n.vote(commitment, ok))}   # BFT: need 2f+1
        if len(sigs) < self.quorum: self._go(b, S.REJECTED); b["reason"] = "no BFT quorum"; return b
        b["proof"] = dict(commitment=commitment, sigs=sigs); self._go(b, S.BATCHED)

        tid, expiry = sell["token_id"], self.clock() + self.ttl
        try:
            self.cb.earmark(b["id"], buy["party"], buy["price"])
            self.dx.lock_asset(b["id"], tid, expiry)
        except LedgerError as e:
            return self._abort(b, tid, str(e))
        self._go(b, S.LEGS_RESERVED)

        if self.clock() >= expiry: return self._abort(b, tid, "expiry reached")
        self._go(b, S.COMMIT_DECIDED)                                     # point of no return
        for _ in range(max_retries):                                      # no rollback: retry until final
            try:
                b["receipt"] = self.cb.debit(b["id"], sell["party"], b["proof"])
                self.dx.settle_leg(b["id"], tid, buy["party"], b["proof"]); break
            except LedgerError: time.sleep(0.01)
        else: raise RuntimeError("settlement stuck - operator escalation")
        self._go(b, S.SETTLED); return b

    def _abort(self, b, tid, reason):
        self._go(b, S.ABORTING); b["reason"] = reason
        self.dx.release_lock(b["id"], tid); self.cb.release_earmark(b["id"])
        self._go(b, S.RELEASED); return b
