# `examples/basic_example.py` – one bond through every block
`python3 examples/basic_example.py` (`--backend peer` to use a real Drunix network). Every chain call is traced as `[chain] function(args)`.

```mermaid
flowchart TD
  S0["0 Setup<br/>keys, KYC, list asset, mint"] --> S1["1 Kalman<br/>quotes -> r0"]
  S1 --> S2["2 Vasicek<br/>one step by hand +<br/>MC vs closed form check"]
  S2 --> S3["3 Bond price<br/>cashflow x discount table"]
  S3 --> S4["4 VaR/ES -> LTV<br/>oracle.update_risk"]
  S4 --> S5["5 CSE DvP<br/>3 of 4 nodes sign -> SETTLED"]
  S5 --> S6["6 Collateral<br/>borrow, refused release,<br/>repay, release"]
```
Story: IssuerB sells 1000 units of a 2-year 8% bond (face 100) to BankA for ≈ INR 98,316; BankA pledges it, borrows half the limit, repays and gets it back. Numbers shown: r0 7.12%, price 98.316, LTV 85%.
