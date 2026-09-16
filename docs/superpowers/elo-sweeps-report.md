# elo-sweeps report

Every Elo setting moved one at a time away from the default, scored against the
default on identical horizon-0 matches, 2016-2025 (3,760 matches), with the
goal-difference line fitted on the previous season (elo-ten-seasons). Negative
means the level beats the default. Produced by `entrypoints/elo_backtest.py --sweep`.

**Pass rule, fixed in the ticket before running:** better than the default in at
least 8 of 10 seasons AND a pooled 95% interval clear of zero.

Default Elo, pooled RPS 0.20745; incumbent 0.21104. Gate: the default reproduces `elo_ten_seasons.csv` on every season.

**Levels that pass: 0 of 24.**

| setting | level | − default | 95% CI | better in | find 2016-20 | confirm 2021-25 | verdict |
|---|---|---:|---|---:|---:|---:|---|
| k | 10 | -0.00016 | [-0.00084, +0.00052] | 6/10 | -0.00051 | +0.00019 | no clear difference |
| k | 15 | -0.00019 | [-0.00049, +0.00011] | 6/10 | -0.00037 | -0.00001 | no clear difference |
| k | 25 | +0.00027 | [+0.00002, +0.00050] | 2/10 | +0.00042 | +0.00011 | worse, clearly |
| k | 30 | +0.00055 | [+0.00010, +0.00097] | 2/10 | +0.00083 | +0.00026 | worse, clearly |
| k | 40 | +0.00108 | [+0.00033, +0.00180] | 1/10 | +0.00153 | +0.00063 | worse, clearly |
| home_advantage | 50 | -0.00000 | [-0.00013, +0.00012] | 6/10 | +0.00008 | -0.00008 | no clear difference |
| home_advantage | 70 | -0.00000 | [-0.00006, +0.00005] | 6/10 | +0.00003 | -0.00004 | no clear difference |
| home_advantage | 100 | +0.00001 | [-0.00005, +0.00006] | 3/10 | -0.00003 | +0.00005 | no clear difference |
| home_advantage | 120 | +0.00002 | [-0.00011, +0.00016] | 3/10 | -0.00007 | +0.00012 | no clear difference |
| margin_ladder | flat 1/1/1 | +0.00034 | [-0.00020, +0.00090] | 4/10 | +0.00051 | +0.00018 | no clear difference |
| margin_ladder | mild 1/1.5/2 | -0.00001 | [-0.00016, +0.00013] | 6/10 | +0.00001 | -0.00004 | no clear difference |
| margin_ladder | steep 1/2/3 | +0.00007 | [-0.00005, +0.00019] | 4/10 | +0.00006 | +0.00007 | no clear difference |
| seed_gap | 50 | +0.00005 | [-0.00001, +0.00010] | 3/10 | +0.00011 | -0.00002 | no clear difference |
| seed_gap | 150 | -0.00004 | [-0.00010, +0.00002] | 4/10 | -0.00010 | +0.00001 | no clear difference |
| season_regression | 0.1 | +0.00018 | [+0.00001, +0.00036] | 3/10 | +0.00030 | +0.00007 | worse, clearly |
| season_regression | 0.2 | +0.00034 | [+0.00003, +0.00065] | 4/10 | +0.00054 | +0.00014 | worse, clearly |
| season_regression | 0.33 | +0.00055 | [+0.00008, +0.00101] | 4/10 | +0.00081 | +0.00029 | worse, clearly |
| continental_weight | 0.5 | -0.00003 | [-0.00022, +0.00016] | 4/10 | -0.00005 | -0.00001 | no clear difference |
| continental_weight | 1.5 | +0.00009 | [-0.00007, +0.00026] | 2/10 | +0.00007 | +0.00012 | no clear difference |
| copa_weight | 0.5 | -0.00005 | [-0.00021, +0.00010] | 6/10 | -0.00014 | +0.00003 | no clear difference |
| copa_weight | 1.5 | +0.00011 | [-0.00003, +0.00026] | 4/10 | +0.00019 | +0.00003 | no clear difference |
| serie_b_weight | 0.5 | +0.00003 | [-0.00023, +0.00030] | 3/10 | -0.00022 | +0.00028 | no clear difference |
| totals_window | 180 | +0.00049 | [+0.00001, +0.00099] | 3/10 | +0.00071 | +0.00027 | worse, clearly |
| totals_window | 730 | -0.00008 | [-0.00027, +0.00011] | 6/10 | -0.00015 | -0.00000 | no clear difference |

No default is changed by this ticket; adopting a level is a separate decision.
