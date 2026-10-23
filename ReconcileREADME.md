# `cse/reconcile.py` – reconciliation & pricing hook
| Function | What it does |
|---|---|
| `reconcile(cse, dx, cb)` | For every SETTLED batch checks that a Drunix `TRANSFERRED` event **and** a wCBDC receipt exist for the same `batchId` with the same proof reference. Returns `settled, matched, rate, orphans` (orphans = ledger records with no settled batch = reconciliation breaks) |
| `fair_value_from_chain(dx)` | Returns `fair_value(token_id)` = `units × priceE6/1e6` using the oracle price on-chain. Passed to `CSE(fair_value=…, band=0.05)` so mispriced trades are rejected |

```mermaid
flowchart TD
  S["CSE batches in SETTLED"] --> M{"Drunix TRANSFERRED<br/>for batchId?"}
  M -- yes --> R{"wCBDC receipt<br/>same batchId + proof?"}
  M -- no --> X["break"]
  R -- yes --> OK["matched"]
  R -- no --> X
  L["ledger records<br/>without settled batch"] --> OR["orphans"]
  OK --> RATE["rate = matched / settled"]
```
Works on `MockDrunix` (reads `events`); with the real chain, build the same view from chaincode events.
