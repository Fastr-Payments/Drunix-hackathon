# `cse/engine.py` – Coordination State Engine
**Purpose.** Coordinates both legs of a trade without ever holding value. Implements Amit's state machine.

| Item | Role |
|---|---|
| `S`, `LEGAL` | States and the only legal transitions (illegal move → assertion) |
| `sign(priv, intent)` | Wallet signature over canonical intent |
| `merkle_root(hashes)` | Batch commitment over sorted intent hashes |
| `DCENode.vote(commitment, valid)` | Node signs only if **its own** call to `valid()` is true; `byzantine=True` withholds |
| `CSE._validate(intents)` | Signature, KYC, intent expiry, atomicity rule, token/price match, ownership, funds, **price band vs model value** |
| `CSE.execute_dvp(buy, sell)` | The whole flow (below) |
| `CSE._abort(b, token, reason)` | ABORTING → release lock + earmark → RELEASED |
| `CSE._go` / `verify_audit` | Transition + hash-chained audit entry; recompute chain to detect tampering |

```mermaid
stateDiagram-v2
  [*] --> INTENT_RECEIVED
  INTENT_RECEIVED --> VALIDATED: validate ok
  INTENT_RECEIVED --> REJECTED: constraint / KYC / price band fail
  VALIDATED --> BATCHED: 2f+1 node signatures
  VALIDATED --> REJECTED: no quorum
  BATCHED --> LEGS_RESERVED: earmark ACK + lock ACK
  BATCHED --> ABORTING: lock or earmark fail
  LEGS_RESERVED --> COMMIT_DECIDED: before expiry
  LEGS_RESERVED --> ABORTING: expiry reached
  COMMIT_DECIDED --> SETTLED: debit + settle_leg (retry until final)
  ABORTING --> RELEASED: ReleaseLock + ReleaseEarmark
  SETTLED --> [*]
  RELEASED --> [*]
  REJECTED --> [*]
```
```mermaid
sequenceDiagram
  participant M as Marketplace
  participant C as CSE
  participant N as DCE nodes x4
  participant W as wCBDC
  participant D as Drunix
  M->>C: signed BUY + SELL intents
  C->>N: commitment (each re-validates)
  N-->>C: >= 3 signatures = proof
  C->>W: Earmark(batch)
  C->>D: LockAsset(batch, expiry)
  Note over C: COMMIT_DECIDED (no rollback)
  C->>W: Debit(batch, proof)
  C->>D: SettleLeg(proof) verifies on-chain
  C-->>M: SETTLED + proof ref
```
**Limits.** Quorum signing is not a full BFT protocol; one pair per batch; `_validate` is shared code so a bug there is shared by all nodes (real nodes run their own implementation).
