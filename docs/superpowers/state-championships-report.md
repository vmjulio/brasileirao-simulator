# state-championships report

Default Elo against four arms that add matches to the store (ratings, goal line and
totals all read it), on identical horizon-0 Série A matches. Negative favours the arm.
Produced by `entrypoints/elo_backtest.py --state`.

**Pass rules, fixed on the board before any data was pulled:** `copa-all-rounds` better
in 8 of 10 seasons (2016-2025) and a pooled 95% interval clear of zero; state arms
better in 5 of 6 (2020-2025) and a pooled interval over 2020-2025 clear of zero.
Gates: the default reproduces `elo_ten_seasons.csv`; the state arms equal it on 2016-2019.

| arm | tested on | − default | 95% CI | better in | verdict |
|---|---|---:|---|---:|---|
| copa-all-rounds | 2016-2025 | -0.00005 | [-0.00043, +0.00033] | 5/10 | does not pass |
| state-1.0 | 2020-2025 | +0.00016 | [-0.00105, +0.00138] | 3/6 | does not pass |
| state-0.5 | 2020-2025 | -0.00018 | [-0.00094, +0.00060] | 4/6 | does not pass |
| all-1.0 | 2020-2025 | +0.00023 | [-0.00100, +0.00153] | 3/6 | does not pass |

Per season (arm − default):

- copa-all-rounds: {2016: -0.0003, 2017: 0.00048, 2018: -0.00129, 2019: -0.00065, 2020: 0.00033, 2021: -0.00021, 2022: 0.0007, 2023: -0.00027, 2024: 0.00027, 2025: 0.00043}
- state-1.0: {2020: -0.00067, 2021: -0.0008, 2022: 0.00053, 2023: -0.00194, 2024: 0.00069, 2025: 0.00313}
- state-0.5: {2020: -0.00069, 2021: -0.00052, 2022: -0.00024, 2023: -0.00165, 2024: 0.00036, 2025: 0.00165}
- all-1.0: {2020: -0.00054, 2021: -0.00124, 2022: 0.00159, 2023: -0.0023, 2024: 0.00084, 2025: 0.00306}

No default is changed by this ticket.
