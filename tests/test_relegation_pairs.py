import pandas as pd
import pytest

from brasileirao_simulator.entrypoints import relegation_pairs as rp


def test_round_as_of_uses_local_dates_and_played_matches():
    fixtures = pd.DataFrame({
        "fixture_date": ["2026-09-07T22:00:00+00:00", "2026-09-15T01:30:00+00:00", "2026-09-16T22:00:00+00:00"],
        "league_round": ["Regular Season - 26", "Regular Season - 27", "Regular Season - 28"],
        "goals_home": [1, 2, None],
    })
    # 01:30 UTC on the 15th is 22:30 on the 14th in Brazil.
    assert rp.round_as_of(fixtures, "2026-09-14") == 27
    assert rp.round_as_of(fixtures, "2026-09-07") == 26
    assert rp.round_as_of(fixtures, "2026-01-01") is None


def test_data_document_has_the_agreed_keys():
    doc = rp.data_document(2026, "2026-09-14", ("Gremio", "Internacional"),
                           {"neither": 1, "only_Gremio": 2, "only_Internacional": 3, "both": 4}, 27, "2026-09-15")
    assert doc == {
        "question": "relegation_pairs", "season": 2026, "as_of": "2026-09-14", "round": 27,
        "clubs": ["Gremio", "Internacional"],
        "counts": {"neither": 1, "only_a": 2, "only_b": 3, "both": 4},
        "generated_by": "relegation_pairs.py", "generated_at": "2026-09-15",
    }


def test_data_document_refuses_a_date_before_any_round():
    with pytest.raises(ValueError, match="no round"):
        rp.data_document(2026, "2026-01-01", ("Gremio", "Internacional"),
                         {"neither": 1, "only_Gremio": 2, "only_Internacional": 3, "both": 4}, None, "2026-09-15")
