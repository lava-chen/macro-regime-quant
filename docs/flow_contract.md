# Capital-flow engine contract

## Objective

Estimate where capital is moving using observable holdings, transactions and balance-sheet identities. The system must never claim it can observe every dollar in real time.

## Evidence classes

Every flow observation is one of:

- **observed** — directly reported or mechanically derived from a disclosed transaction/position;
- **inferred** — estimated from incomplete information.

Both must retain source and confidence metadata.

## Stock-flow identity

The core accounting relation is:

~~~text
current_stock
= previous_stock
+ net_flow
+ valuation_effect
+ other_adjustments
~~~

Therefore:

~~~text
net_flow
= change_in_stock
- valuation_effect
- other_adjustments
~~~

This prevents a common error: interpreting an increase in AUM or holdings as an equal amount of external capital inflow.

## Initial observed/inferred inputs

Potential observed series:
- ETF creation/redemption or shares outstanding;
- mutual-fund subscription/redemption where available;
- margin balances;
- futures/open interest;
- options/open interest;
- corporate issuance and buybacks;
- reported institutional holdings;
- bank credit and deposits;
- money-market assets;
- FX reserves.

Potential inferred series:
- valuation-adjusted AUM flows;
- sector allocation shifts;
- investor-bucket positioning;
- ownership-network transitions.

## Network representation

Long-term the flow engine can represent nodes such as:

~~~text
household
bank
corporate
government
foreign
mutual_fund
ETF
insurance
pension
hedge_fund
~~~

and asset buckets such as:

~~~text
cash
deposit
bond
equity
commodity
real_estate
FX
~~~

A directed edge is a flow estimate with date, amount, currency, evidence type and confidence.

## Research rule

Observed and inferred flow data must never be silently mixed into one raw field. Any aggregate Flow Score must preserve the ability to decompose the score back to underlying evidence.
