from brasileirao_simulator.domain.queries import Queries


def test_season_is_substituted_into_sql():
    sql = Queries(2026).tidy_fixtures()

    assert "'2026'" in sql
    assert "'2025'" in sql, "the previous season must appear for the lookback union"


def test_no_placeholder_survives_substitution():
    """An unsubstituted placeholder is a silent wrong answer, not a crash:
    DuckDB would fail or filter on nothing depending on where it landed."""
    for sql in (Queries(2026).tidy_fixtures(), Queries(2026).standings()):
        assert "$" not in sql


def test_stale_season_literals_are_gone():
    sql = Queries(2026).tidy_fixtures()

    assert "'2024'" not in sql, "a hardcoded 2024 survived parameterization"


def test_standings_filters_the_requested_season():
    assert "season = '2026'" in Queries(2026).standings()
