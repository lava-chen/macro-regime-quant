# Valuation engine contract

## Objective

Estimate a range of plausible intrinsic values and make the market's embedded assumptions explicit. A valuation engine is not a price target generator.

## Point-in-time requirement

A valuation at date t may use only fundamental observations with:

~~~text
available_date <= t
~~~

Restated or revised accounts must not silently overwrite what was historically known.

## Company-type routing

The default methods are business-model dependent:

| Business type | Preferred methods |
|---|---|
| General mature company | DCF + Reverse DCF |
| High-growth company | Reverse DCF + DCF |
| Bank | Residual income / P-B framework |
| Insurance | Embedded value |
| REIT | AFFO / NAV |
| Commodity producer | Normalized-cycle / asset NAV |

Only DCF and Reverse DCF are implemented in platform v0. Other methods are explicit future contracts, not placeholders presented as finished models.

## Standard output

Each company valuation should eventually expose:

~~~text
company_id
as_of_date
valuation_method
value_per_share_low
value_per_share_base
value_per_share_high
market_price
margin_of_safety
confidence
model_version
~~~

Additional quality, growth, leverage and capital-allocation scores are separate signals and should not be hidden inside intrinsic value.

## DCF baseline

For FCFF:

~~~text
EV = sum(FCFF_t / (1 + WACC)^t) + TV / (1 + WACC)^N
Equity Value = EV - Net Debt
Value Per Share = Equity Value / Diluted Shares
~~~

Terminal growth must be below the discount rate.

## Reverse DCF

Reverse DCF solves for the growth path required to justify the current market price.

This reframes the question from "What growth do I predict?" to "What growth is the market already pricing in?"

The comparison between implied growth and a defensible fundamental range is a first-class research signal.
