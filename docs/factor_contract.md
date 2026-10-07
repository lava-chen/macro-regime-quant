# Macro factor contract v1

## Purpose

Translate the AD-AS intuition into observable, point-in-time-safe state variables without pretending that an observed macro series *is* AD or AS.

The baseline state is deliberately simple:

- **Growth factor**: direction of real activity / demand-realization.
- **Inflation factor**: direction of broad price pressure.
- **Liquidity / real-rate factors**: financial transmission conditions.
- **Regime**: the sign pair `(growth, inflation)`.

## Information-set rule

Every raw macro observation must first obtain an `available_date`. A factor at time `t` may only use observations whose `available_date <= t`.

Normalization uses an expanding history, never full-sample statistics:

```text
z_t = (x_t - mean[x_{<t}]) / std[x_{<t}]
```

The baseline implementation deliberately uses history through `t-1`.

## Component transformations

Typical transformations before z-scoring:

- activity levels: YoY growth or diffusion-index distance from 50;
- rates/yields: level and/or 3M change depending on economic meaning;
- credit/liquidity: YoY growth, impulse, spread or change;
- signs are oriented so **higher factor = more of the named state**.

Example: a fall in unemployment is pro-growth, so `unemployment_change` enters Growth with sign `-1`.

## Composite factor

For standardized component scores `z_i,t`:

```text
F_t = sum(w_i * z_i,t for available i) / sum(w_i for available i)
```

A minimum number of available components is required. Missing series are not zero.

Weights in v1 are transparent priors, not optimized coefficients. We will only estimate weights after out-of-sample benchmarks exist.

## Four-regime baseline

| Growth | Inflation | Regime |
|---|---|---|
| > 0 | <= 0 | Goldilocks |
| > 0 | > 0 | Reflation |
| <= 0 | > 0 | Stagflation |
| <= 0 | <= 0 | Recession |

These labels are research shorthand, not declarations that the official economy is literally in recession or stagflation.

## Country-specific reasoning

China and the US do **not** use identical observable inputs.

China needs more weight on credit impulse, M1/M2, property/fixed-asset investment and policy/fiscal transmission. The US needs more weight on labor data, market real rates, credit spreads and Fed transmission.

We keep the latent concepts comparable while allowing the measurement model to differ.

## Validation sequence

1. Confirm each component is aligned by release date.
2. Plot components and composite factors.
3. Verify known macro episodes qualitatively.
4. Freeze factor definitions.
5. Measure 3M/6M/12M forward asset returns by regime.
6. Walk-forward evaluation.
7. Only then consider PCA, learned weights or HMM/Markov switching.
