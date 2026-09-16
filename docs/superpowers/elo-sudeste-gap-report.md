# elo-sudeste-gap report

Every Elo setting moved one at a time away from the default, scored against the
default on identical horizon-0 matches, 2016-2025 (3,760 matches), with the
goal-difference line fitted on the previous season (elo-ten-seasons). Negative
means the level beats the default. Produced by `entrypoints/elo_backtest.py`.

**Pass rule, fixed in the ticket before running:** better than the default in at
least 8 of 10 seasons AND a pooled 95% interval clear of zero.

Default Elo, pooled RPS 0.20745; incumbent 0.21104. Gate: the default reproduces `elo_ten_seasons.csv` on every season.

**Levels that pass: 1 of 4.**

| setting | level | − default | 95% CI | better in | find 2016-20 | confirm 2021-25 | verdict |
|---|---|---:|---|---:|---:|---:|---|
| sudeste_gap | 25 | -0.00048 | [-0.00087, -0.00010] | 9/10 | -0.00047 | -0.00049 | **passes** |
| sudeste_gap | 50 | -0.00050 | [-0.00127, +0.00026] | 7/10 | -0.00051 | -0.00049 | no clear difference |
| sudeste_gap | 75 | -0.00007 | [-0.00122, +0.00107] | 6/10 | -0.00013 | -0.00001 | no clear difference |
| sudeste_gap | 100 | +0.00079 | [-0.00073, +0.00232] | 4/10 | +0.00067 | +0.00092 | no clear difference |

No default is changed by this ticket; adopting a level is a separate decision.
