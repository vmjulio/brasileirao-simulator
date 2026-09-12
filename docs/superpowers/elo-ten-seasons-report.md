# elo-ten-seasons report

Elo with the goal-difference line fitted on the season before each scored
season, against the incumbent, horizon 0, identical matches. Negative means
Elo is better. Produced by `entrypoints/elo_backtest.py --ten-seasons`.

## Gate

The shipped fixed-2019 line, run through this harness, reproduces the committed
four-arm `rps_elo`, `rps_current` and match counts on 2020-2025 to 1e-9.

## The scheme change on its own (2020-2025)

- fixed-2019 Elo vs incumbent: -0.00346 [-0.00618, -0.00063], better in 6 of 6
- previous-season Elo vs incumbent: -0.00392 [-0.00643, -0.00131], better in 6 of 6
- previous-season minus fixed-2019: -0.00046 [-0.00112, +0.00018], previous-season better in 4 of 6

## Ten seasons (2016-2025)

| season | matches | line fitted on | RPS Elo | RPS incumbent | Elo − incumbent | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| 2016 | 375 | 2015 | 0.2047 | 0.2075 | -0.0027 | [-0.0069, +0.0014] |
| 2017 | 378 | 2016 | 0.2281 | 0.2258 | +0.0023 | [-0.0043, +0.0089] |
| 2018 | 377 | 2017 | 0.1875 | 0.1935 | -0.0061 | [-0.0103, -0.0018] |
| 2019 | 376 | 2018 | 0.1966 | 0.2026 | -0.0060 | [-0.0125, +0.0006] |
| 2020 | 377 | 2019 | 0.2125 | 0.2160 | -0.0034 | [-0.0098, +0.0029] |
| 2021 | 377 | 2020 | 0.2094 | 0.2120 | -0.0026 | [-0.0088, +0.0038] |
| 2022 | 377 | 2021 | 0.2067 | 0.2109 | -0.0042 | [-0.0108, +0.0026] |
| 2023 | 373 | 2022 | 0.2179 | 0.2200 | -0.0021 | [-0.0095, +0.0055] |
| 2024 | 376 | 2023 | 0.2094 | 0.2143 | -0.0049 | [-0.0102, +0.0004] |
| 2025 | 374 | 2024 | 0.2016 | 0.2079 | -0.0063 | [-0.0118, -0.0008] |
| 2026 (partial) | 241 | 2025 | 0.2044 | 0.2098 | -0.0054 | [-0.0129, +0.0022] |

**Pooled 2016-2025, 3,760 matches: -0.00360 [-0.00553, -0.00170]; Elo better in 9 of 10 seasons.** Find half (2016-2020): -0.00317 [-0.00566, -0.00065]; confirm half (2021-2025): -0.00402 [-0.00679, -0.00120].

## Against chancedegol (2016-2025)

Same Elo, scored on chancedegol's own matches with
`benchmark_chancedegol.py --model elo`. Negative means Elo is better.

| season | matches | RPS Elo | RPS chancedegol | Elo − chancedegol | 95% CI |
|---|---:|---:|---:|---:|---|
| 2016 | 374 | 0.2050 | 0.2056 | -0.0006 | [-0.0064, +0.0055] |
| 2017 | 378 | 0.2262 | 0.2298 | -0.0036 | [-0.0093, +0.0021] |
| 2018 | 376 | 0.1902 | 0.1889 | +0.0013 | [-0.0044, +0.0067] |
| 2019 | 372 | 0.1963 | 0.1978 | -0.0015 | [-0.0060, +0.0031] |
| 2020 | 377 | 0.2120 | 0.2144 | -0.0024 | [-0.0087, +0.0038] |
| 2021 | 377 | 0.2090 | 0.2104 | -0.0014 | [-0.0069, +0.0039] |
| 2022 | 376 | 0.2069 | 0.2048 | +0.0021 | [-0.0033, +0.0074] |
| 2023 | 373 | 0.2179 | 0.2183 | -0.0004 | [-0.0059, +0.0049] |
| 2024 | 376 | 0.2094 | 0.2121 | -0.0028 | [-0.0073, +0.0018] |
| 2025 | 373 | 0.2017 | 0.2088 | -0.0071 | [-0.0129, -0.0011] |
| 2026 (partial) | 241 | 0.2044 | 0.2080 | -0.0036 | [-0.0087, +0.0015] |

**Pooled 2016-2025, 3,752 matches: -0.00163 [-0.00335, +0.00011]; Elo better in 8/10 seasons.**
For contrast, the incumbent against the same forecaster: +0.00193 [-0.00035, +0.00424], better in 2 of 10.

## Caveat

Before 2019 the store has no Libertadores or Sudamericana, and before 2016 no
Copa do Brasil, so the ratings behind 2016-2019 forecasts come from Série A and
Série B (plus the cup from 2016). The pre-2019 arm is narrower than the one
measured on 2020-2025. Dropped matches per season: 2016: 0, 2017: 0, 2018: 0, 2019: 0, 2020: 0, 2021: 0, 2022: 0, 2023: 0, 2024: 0, 2025: 0, 2026: 0.
