import os

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter


def test_results_are_written_under_the_season(tmp_path):
    adapter = PickleAdapter(str(tmp_path), season=2026)
    adapter.save_results({"brasileirao_title": {"Flamengo": 3}}, strategy="average")

    assert os.path.isfile(tmp_path / "2026" / "average_results.pkl")


def test_seasons_do_not_collide(tmp_path):
    """The no-suffix case wrote average_results.pkl for every season, so a 2026
    run silently overwrote the 2025 file."""
    PickleAdapter(str(tmp_path), season=2025).save_results({"a": 1}, strategy="average")
    PickleAdapter(str(tmp_path), season=2026).save_results({"b": 2}, strategy="average")

    assert PickleAdapter(str(tmp_path), season=2025).load_results("average") == {"a": 1}
    assert PickleAdapter(str(tmp_path), season=2026).load_results("average") == {"b": 2}


def test_missing_results_load_as_none(tmp_path):
    assert PickleAdapter(str(tmp_path), season=2026).load_results("average") is None


def test_suffix_still_separates_dates(tmp_path):
    adapter = PickleAdapter(str(tmp_path), season=2026)
    adapter.save_results({"a": 1}, strategy="average", suffix="2026-03-01")

    assert os.path.isfile(tmp_path / "2026" / "average_results_2026-03-01.pkl")
