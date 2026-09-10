"""Gate for refresh-competitions (E7/T7.3): one command that shards the
current season's live pull into `competitions/{league}/{season}.csv` and
reloads `MatchStore` off the result, idempotently, and that never touches
the API-Football key unless a caller explicitly says --pull.

TEST MATERIAL. `tests/fixtures/refresh_competitions/processed_fixtures_2026_{72,73,13,11}.csv`
are verbatim copies of the real lean-pype exports that already produced
`src/files/datasets/competitions/{72,73,13,11}/2026.csv` (confirmed
byte-for-byte outside this suite, against the live host checkout of
lean-pype, before committing these copies). Every test here shards from
those files into a temp copy of the real shard tree
(`src/files/datasets/competitions`) - `shards_copy` below - so nothing a
test does ever mutates the committed shards, and the committed shards
double as the "known good" comparison target for the no-op gate.

NO SUBPROCESS EVER RUNS IN THIS FILE. `pull()` is only reachable when a test
sets `do_pull=True`, and every such test monkeypatches `pull` itself first -
so this file, by construction, cannot shell out to docker-compose or spend
the owner's API-Football quota.
"""

import csv
import os
import shutil
from pathlib import Path

import pytest

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.entrypoints import refresh_competitions as rc

SEASON = 2026
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "refresh_competitions"
COMMITTED_SHARDS = f"{DATASETS_PATH}/competitions"

# fixture_id 1520609: Vila Nova 2-2 CRB, Serie B 2026, "Regular Season - 1",
# FT. Used by the duplicate/corrected-score test below.
CRB_FIXTURE_ID = "1520609"

# The real 2026 shards for 73, 13 and 11 use round labels
# domain/competitions.py's _stage_rank does not recognise yet - "1/256-finals"
# (Copa do Brasil) and "Qualification Round 1/2/3" (Libertadores,
# Sudamericana). MatchStore raises on them by design (see its module
# docstring: an unrecognised stage name is "a data change worth looking at,
# not a match that should be silently included or silently dropped").
# test_2026_cup_round_labels_are_not_yet_recognised_by_stage_rank below
# documents this as a pre-existing gap in a file this ticket may not touch;
# the MatchStore-touching tests here route around it by restricting to
# league 72 (Serie B has no round filter, so it never calls _stage_rank).
_UNLOADABLE_2026_CUPS = (73, 13, 11)


def _restrict_to_serie_b_2026(shards_copy: Path) -> None:
    """Remove the leagues MatchStore cannot load for 2026 yet (see
    `_UNLOADABLE_2026_CUPS` above), so a test exercises refresh_competitions'
    own idempotency/dedupe logic rather than tripping over that unrelated,
    out-of-scope gap."""
    for league_id in _UNLOADABLE_2026_CUPS:
        (shards_copy / str(league_id) / f"{SEASON}.csv").unlink()


@pytest.fixture
def source_dir(tmp_path) -> Path:
    """A writable copy of the four live-pull exports - tests mutate this
    freely without touching the checked-in fixtures."""
    dest = tmp_path / "source"
    dest.mkdir()
    for league_id in rc.LIVE_LEAGUES:
        name = f"processed_fixtures_{SEASON}_{league_id}.csv"
        shutil.copy(FIXTURES_DIR / name, dest / name)
    return dest


@pytest.fixture
def shards_copy(tmp_path) -> Path:
    """A temp copy of the real `competitions/` shard tree - every test
    writes here, never to `src/files/datasets/competitions` itself."""
    dest = tmp_path / "competitions"
    shutil.copytree(COMMITTED_SHARDS, dest)
    return dest


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def _write_rows(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _snapshot(root: Path) -> dict:
    """`{relpath: (size, mtime_ns)}` for every file under `root` - cheap
    enough to call before and after a step that must write nothing."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _shard_bytes(root: Path, leagues) -> dict:
    return {league_id: (root / str(league_id) / f"{SEASON}.csv").read_bytes() for league_id in leagues}


# --------------------------------------------------------------------------
# shard + reload


def test_shard_all_reproduces_the_committed_shards_byte_for_byte(shards_copy, source_dir):
    """The live-pull exports in tests/fixtures are the exact source that
    produced the committed shards - sharding them again must be a no-op
    against what is already on disk."""
    before = _shard_bytes(shards_copy, rc.LIVE_LEAGUES)

    rc.shard_all(SEASON, rc.LIVE_LEAGUES, str(source_dir), str(shards_copy))

    after = _shard_bytes(shards_copy, rc.LIVE_LEAGUES)
    assert before == after


def test_refresh_twice_is_a_noop(shards_copy, source_dir):
    _restrict_to_serie_b_2026(shards_copy)
    leagues = (72,)

    coverage_1 = rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    bytes_1 = _shard_bytes(shards_copy, leagues)
    matches_1 = MatchStore(root=str(shards_copy)).matches

    coverage_2 = rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    bytes_2 = _shard_bytes(shards_copy, leagues)
    matches_2 = MatchStore(root=str(shards_copy)).matches

    assert bytes_1 == bytes_2
    assert matches_1.equals(matches_2)
    assert coverage_1 == coverage_2


def test_superset_source_admits_exactly_one_more_match_then_is_idempotent(shards_copy, source_dir):
    _restrict_to_serie_b_2026(shards_copy)
    leagues = (72,)

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    matches_before = MatchStore(root=str(shards_copy)).matches

    league_72_path = source_dir / f"processed_fixtures_{SEASON}_72.csv"
    fieldnames, rows = _read_rows(league_72_path)
    template = dict(rows[0])
    new_fixture_id = str(max(int(row["fixture_id"]) for row in rows) + 1)
    template.update(
        fixture_id=new_fixture_id,
        fixture_status_short="FT",
        fixture_status_long="Match Finished",
        goals_home="3",
        goals_away="1",
        score_fulltime_home="3",
        score_fulltime_away="1",
    )
    rows.append(template)
    _write_rows(league_72_path, fieldnames, rows)

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    matches_after = MatchStore(root=str(shards_copy)).matches

    assert len(matches_after) == len(matches_before) + 1
    new_row = matches_after[matches_after["fixture_id"] == int(new_fixture_id)]
    assert len(new_row) == 1
    assert new_row["home_goals"].iloc[0] == 3
    assert new_row["away_goals"].iloc[0] == 1

    # Re-running against the same (now-superset) source is again a no-op.
    bytes_after_second_run = _shard_bytes(shards_copy, leagues)
    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    assert _shard_bytes(shards_copy, leagues) == bytes_after_second_run
    assert MatchStore(root=str(shards_copy)).matches.equals(matches_after)


def test_repeated_fixture_id_with_corrected_score_updates_only_that_match(shards_copy, source_dir):
    _restrict_to_serie_b_2026(shards_copy)
    leagues = (72,)

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    matches_before = MatchStore(root=str(shards_copy)).matches

    league_72_path = source_dir / f"processed_fixtures_{SEASON}_72.csv"
    fieldnames, rows = _read_rows(league_72_path)
    for row in rows:
        if row["fixture_id"] == CRB_FIXTURE_ID:
            assert row["score_fulltime_home"] == "2"
            row["score_fulltime_home"] = "5"
    _write_rows(league_72_path, fieldnames, rows)

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=leagues)
    matches_after = MatchStore(root=str(shards_copy)).matches

    assert len(matches_after) == len(matches_before)

    corrected = matches_after[matches_after["fixture_id"] == int(CRB_FIXTURE_ID)]
    assert corrected["home_goals"].iloc[0] == 5
    assert corrected["away_goals"].iloc[0] == 2

    unaffected_before = matches_before[matches_before["fixture_id"] != int(CRB_FIXTURE_ID)].reset_index(drop=True)
    unaffected_after = matches_after[matches_after["fixture_id"] != int(CRB_FIXTURE_ID)].reset_index(drop=True)
    assert unaffected_before.equals(unaffected_after)


# --------------------------------------------------------------------------
# --dry-run


def test_dry_run_prints_the_plan_and_writes_nothing(shards_copy, source_dir, capsys):
    before = _snapshot(shards_copy)

    coverage = rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), dry_run=True)

    assert coverage is None
    assert _snapshot(shards_copy) == before

    output = capsys.readouterr().out
    assert "[dry-run] pull" in output
    assert f"LEAGUES={','.join(str(l) for l in rc.LIVE_LEAGUES)} SEASONS={SEASON}" in output
    for league_id in rc.LIVE_LEAGUES:
        assert f"[dry-run] shard: {source_dir}/processed_fixtures_{SEASON}_{league_id}.csv" in output
        assert f"{shards_copy}/{league_id}/{SEASON}.csv" in output


def test_dry_run_with_pull_still_writes_nothing_and_never_calls_pull(monkeypatch, shards_copy, source_dir):
    calls = []
    monkeypatch.setattr(rc, "pull", lambda *a, **kw: calls.append((a, kw)))
    before = _snapshot(shards_copy)

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), do_pull=True, dry_run=True)

    assert calls == []
    assert _snapshot(shards_copy) == before


# --------------------------------------------------------------------------
# --pull / --no-pull


def test_pull_is_not_invoked_by_default(monkeypatch, shards_copy, source_dir):
    _restrict_to_serie_b_2026(shards_copy)
    calls = []
    monkeypatch.setattr(rc, "pull", lambda *a, **kw: calls.append((a, kw)))

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=(72,))

    assert calls == []


def test_pull_is_invoked_only_when_do_pull_is_true(monkeypatch, shards_copy, source_dir):
    _restrict_to_serie_b_2026(shards_copy)
    calls = []
    monkeypatch.setattr(rc, "pull", lambda *a, **kw: calls.append((a, kw)))

    rc.refresh(season=SEASON, source_dir=str(source_dir), out_root=str(shards_copy), leagues=(72,), do_pull=True)

    assert len(calls) == 1
    (season, leagues, lean_pype_dir), kwargs = calls[0]
    assert season == SEASON
    assert tuple(leagues) == (72,)


def test_main_defaults_to_no_pull_and_the_current_season(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "refresh", lambda **kw: calls.append(kw))

    rc.main(["--source-dir", "/somewhere"])

    assert len(calls) == 1
    assert calls[0]["do_pull"] is False
    assert calls[0]["dry_run"] is False
    assert calls[0]["source_dir"] == "/somewhere"
    import datetime

    assert calls[0]["season"] == datetime.date.today().year


def test_main_forwards_pull_dry_run_and_season_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "refresh", lambda **kw: calls.append(kw))

    rc.main(["--pull", "--dry-run", "--season", "2030", "--source-dir", "/elsewhere"])

    assert calls[0] == {
        "season": 2030,
        "source_dir": "/elsewhere",
        "do_pull": True,
        "dry_run": True,
    }


def test_main_no_pull_flag_is_available_and_wins(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "refresh", lambda **kw: calls.append(kw))

    rc.main(["--pull", "--no-pull"])

    assert calls[0]["do_pull"] is False


# --------------------------------------------------------------------------
# a documented, out-of-scope gap this ticket routes around rather than fixes


def test_2026_cup_round_labels_are_not_yet_recognised_by_stage_rank():
    """Canary, not a feature test. The real, already-committed 2026 shards
    for 73/13/11 use round labels domain/competitions.py's `_stage_rank`
    does not know (see `_UNLOADABLE_2026_CUPS` above) - MatchStore(), even
    with no refresh_competitions.py involved at all, already raises on them
    today. That module is off-limits to this ticket, so the other tests
    here restrict themselves to league 72 instead of tripping over it.

    If this test ever fails, `_stage_rank` has been extended to cover the
    new labels: delete this test and `_restrict_to_serie_b_2026`, and let
    the other tests exercise every LIVE_LEAGUES league again."""
    with pytest.raises(ValueError, match="unrecognised league_round label"):
        MatchStore(root=COMMITTED_SHARDS)


# --------------------------------------------------------------------------
# small unit tests on the pieces


def test_live_leagues_is_every_competition_except_serie_a():
    assert 71 not in rc.LIVE_LEAGUES
    assert set(rc.LIVE_LEAGUES) == {72, 73, 13, 11}


def test_format_pull_command_matches_the_documented_invocation():
    command = rc.format_pull_command(2026, (72, 73, 13, 11), "/home/x/lean-pype")

    assert command == (
        "LEAGUES=72,73,13,11 SEASONS=2026 docker-compose run --rm "
        '-e LEAGUES -e SEASONS -v "/home/x/lean-pype/app:/app" extraction'
    )
