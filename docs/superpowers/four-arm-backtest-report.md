# four-arm-backtest report

Four arms on identical matches, horizon 0, RPS headline: A (Dixon-Coles, league only), B (Dixon-Coles, all admitted competitions), C (Elo -> two lambdas, all admitted competitions), and current (the shipped same-venue-average model). `xi=0.0065`, arm B refit at `max_iterations=2000`, paired-bootstrap seed 7, 10k draws.

## Per-season

| season | matches | RPS A | RPS B | RPS C (Elo) | RPS current | diff C-B | 95% CI | diff C-current | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| 2020 | 377 | 0.2189 | 0.2162 | 0.2120 | 0.2160 | -0.00417 | [-0.00873, +0.00042] | -0.00391 | [-0.00975, +0.00199] |
| 2021 | 377 | 0.2141 | 0.2103 | 0.2104 | 0.2120 | +0.00005 | [-0.00342, +0.00340] | -0.00159 | [-0.00867, +0.00571] |
| 2022 | 377 | 0.2141 | 0.2073 | 0.2066 | 0.2109 | -0.00062 | [-0.00371, +0.00248] | -0.00425 | [-0.01132, +0.00304] |
| 2023 | 373 | 0.2243 | 0.2188 | 0.2170 | 0.2200 | -0.00179 | [-0.00641, +0.00268] | -0.00302 | [-0.00980, +0.00384] |
| 2024 | 376 | 0.2165 | 0.2109 | 0.2095 | 0.2143 | -0.00139 | [-0.00480, +0.00189] | -0.00478 | [-0.01038, +0.00079] |
| 2025 | 374 | 0.2051 | 0.2033 | 0.2026 | 0.2079 | -0.00066 | [-0.00488, +0.00359] | -0.00532 | [-0.01103, +0.00043] |
| 2026 (partial) | 241 | 0.2102 | 0.2057 | 0.2047 | 0.2098 | -0.00101 | [-0.00572, +0.00387] | -0.00510 | [-0.01354, +0.00331] |

**C better than B in 5/6 full seasons; C better than current in 6/6 full seasons** (2020, 2021, 2022, 2023, 2024, 2025). 2026 is partial and excluded from the pooled figure below.

## Pooled (full seasons only)

| model | RPS | Brier | log loss |
|---|---:|---:|---:|
| A (Dixon-Coles, league) | 0.2155 | 0.6316 | 1.0661 |
| B (Dixon-Coles, all competitions) | 0.2111 | 0.6206 | 1.0334 |
| C (Elo) | 0.2097 | 0.6176 | 1.0290 |
| current | 0.2135 | 0.6259 | 1.0413 |

matches pooled: 2254

## Arm C (Elo) comparisons

- **C vs B (data held equal, estimator differs)**: pooled diff (C - B) -0.00143, 95% CI [-0.00302, +0.00015]. C better in 5/6 full seasons.
- **C vs A (Elo, all-competitions data, vs Dixon-Coles, league-only data)**: pooled diff (C - A) -0.00582, 95% CI [-0.00859, -0.00311].
- **C vs current (Elo vs the shipped model)**: pooled diff (C - current) -0.00381, 95% CI [-0.00635, -0.00114]. C better in 6/6 full seasons.

Dropped for arm C (`dropped_elo`, summed across all 7 scored seasons including partial 2026): 0. Elo lambda fallbacks (`lambda_fallbacks_total`, summed): 0.

## Reproduction gate (arm A / B / current)

Arm A, B and current's RPS/Brier/log-loss and match counts - computed BEFORE arm C's layering, from `score_data_vs_league`'s own summary, so arm C's drops (if any) cannot move them - reproduce `files/exports/dixon_coles_all_vs_league.csv` exactly (abs diff < 1e-9 on RPS, exact match counts) on [2020, 2021, 2022, 2023, 2024, 2025]. **The run raises and stops before writing any export otherwise - this line is only reached when the gate passed.**

## Six-season caveat

**Six full seasons cannot support the eight-of-ten rule; whatever this finds is provisional**, per the ticket.
