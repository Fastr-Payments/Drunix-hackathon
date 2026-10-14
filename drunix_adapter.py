"""Adapter: the same interface as MockDrunix, but every call is a transaction on the bondcc chaincode.
Transport is pluggable. PeerCLITransport shells out to the `peer` binary of a Drunix/Fabric test-network
(Drunix is HLF-2.5 compatible). Replace with a Fabric Gateway gRPC client for production."""
import json, os, subprocess
from .ledgers import LedgerError

class PeerCLITransport:
    def __init__(self, channel="mychannel", chaincode="bondcc", orderer="localhost:7050", peer_flags=None, env=None):
        self.ch, self.cc, self.orderer = channel, chaincode, orderer
        self.flags = peer_flags or []             # e.g. ["--tls","--cafile",CA,"--peerAddresses",...]
        self.env = {**os.environ, **(env or {})}  # CORE_PEER_* identity of the CSE org
    def _run(self, cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, env=self.env)
        if r.returncode != 0: raise LedgerError(r.stderr.strip()[-300:])
        return r.stdout.strip()
    def invoke(self, fn, *args):
        c = ["peer","chaincode","invoke","-o",self.orderer,"-C",self.ch,"-n",self.cc,"--waitForEvent",
             "-c",json.dumps({"function":fn,"Args":[str(a) for a in args]})] + self.flags
        return self._run(c)
    def query(self, fn, *args):
        return self._run(["peer","chaincode","query","-C",self.ch,"-n",self.cc,"-c",json.dumps({"function":fn,"Args":[str(a) for a in args]})])

class DrunixClient:
    """Python method -> chaincode function. Names mirror MockDrunix so CSE code is backend-agnostic."""
    def __init__(self, transport): self.t = transport
    def set_kyc(self, party, ok=True):      self.t.invoke("SetKYC", party, str(ok).lower())
    def list_asset(self, isin, ltv_bps):    self.t.invoke("ListAsset", isin, ltv_bps)
    def update_risk(self, isin, ltv_bps, price_e6): self.t.invoke("UpdateRisk", isin, ltv_bps, price_e6)
    def mint(self, token_id, isin, owner, units):   self.t.invoke("MintToken", token_id, isin, owner, units)
    def lock_asset(self, batch_id, token_id, expiry): self.t.invoke("LockAsset", batch_id, token_id, int(expiry))
    def release_lock(self, batch_id, token_id):     self.t.invoke("ReleaseLock", batch_id, token_id)
    def settle_leg(self, batch_id, token_id, new_owner, proof): self.t.invoke("SettleLeg", batch_id, token_id, new_owner, json.dumps(proof))
    def lock_collateral(self, tid):         self.t.invoke("LockCollateral", tid)
    def borrow(self, tid, amount):          self.t.invoke("Borrow", tid, amount)
    def repay(self, tid, amount):           self.t.invoke("Repay", tid, amount)
    def release_collateral(self, tid):      self.t.invoke("ReleaseCollateral", tid)
    def liquidate(self, tid):               self.t.invoke("Liquidate", tid)
    def get_token(self, tid):
        try: return json.loads(self.t.query("GetToken", tid))
        except (LedgerError, ValueError): return None
    def get_position(self, tid):
        try: return json.loads(self.t.query("GetPosition", tid))
        except (LedgerError, ValueError): return None
