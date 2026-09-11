"""Parsing Wikipedia's Libertadores and Sudamericana pages. The end-to-end
check - 2019 against API-Football - runs inside the builder (`--check`)."""

import datetime as dt

from brasileirao_simulator.entrypoints.build_wikipedia_continental import (
    LIBERTADORES, SUDAMERICANA, _clock, _date, _goals_by_90, _round_label, parse_page,
)


def test_dates_in_both_formats():
    assert _date("{{Start date|2017|3|14|df=y}}") == dt.date(2017, 3, 14)
    assert _date("February 17, 2015") == dt.date(2015, 2, 17)
    assert _date("17 February 2015") == dt.date(2015, 2, 17)
    assert _date("TBD") is None


def test_kickoff_clock_in_both_formats():
    assert _clock("{{UTZ|21:00|-3}}") == (21, 0, -3.0)
    assert _clock("20:45 [[UTC−06:00|UTC−6]]") == (20, 45, -6.0)
    assert _clock("") is None


def test_goals_by_the_ninetieth_minute_count_stoppage_time_not_extra_time():
    text = "*A {{goal|68}}\n*B {{goal|90+3}}\n*C {{goal|109}}\n*D {{goal|120+2}}\n*E {{goal|12|o.g.}}"
    assert _goals_by_90(text) == 3


BOX = """
===Group 1===
{{Football box
|date       = {{Start date|2018|3|1|df=y}}
|time       = {{UTZ|21:45|-3}}
|team1      = [[Clube de Regatas do Flamengo|Flamengo]] {{flagicon|BRA}}
|score      = 2–2
|team2      = {{flagicon|ARG}} [[Club Atlético River Plate|River Plate]]
|goals1     = *X {{goal|10}}
|goals2     = *Y {{goal|20}}
|stadium    = [[Estádio do Maracanã]], [[Rio de Janeiro]]
}}
"""


def test_a_football_box_becomes_a_utc_match():
    [row] = parse_page(BOX, "2018 Copa Libertadores group stage")
    assert row["kickoff_utc"] == dt.datetime(2018, 3, 2, 0, 45)   # 21:45 at UTC-3
    assert row["team1"]["name"] == "Flamengo" and row["team1"]["country"] == "BRA"
    assert row["team2"]["article"] == "Club Atlético River Plate" and row["team2"]["country"] == "ARG"
    assert row["ninety"] == (2, 2) and row["status"] == "FT" and row["group"] == "1"
    assert row["venue_city"] == "Rio de Janeiro"


def test_module_invoked_boxes_and_flags_parse_too():
    text = BOX.replace("{{Football box", "{{#invoke:football box|main").replace("{{flagicon|ARG}}", "{{#invoke:flag|fbaicon|ARG}}")
    [row] = parse_page(text, "2021 Copa Libertadores group stage")
    assert row["team2"]["country"] == "ARG"


def test_awarded_matches_are_marked_so_the_store_drops_them():
    text = BOX.replace("|score      = 2–2", "|score      = 3–0<br />Awarded")
    [row] = parse_page(text, "t")
    assert row["status"] == "AWD"


def test_every_flag_template_spelling_gives_a_country():
    from brasileirao_simulator.entrypoints.build_wikipedia_continental import _team

    for flag in ("{{flagicon|URU}}", "{{fbaicon|URU}}", "{{Fba|URU}}", "{{Fbaicon|URU}}", "{{#invoke:flag|fbaicon|URU}}",
                 "{{flagicon|URU|football}}"):
        assert _team(f"[[Club Nacional de Football|Nacional]] {flag}")["country"] == "URU", flag


def test_a_minus_sign_score_parses_and_a_cancelled_match_is_dropped():
    [row] = parse_page(BOX.replace("|score      = 2–2", "|score      = 1−0"), "t")
    assert row["ninety"] == (1, 0)
    [row] = parse_page(BOX.replace("|score      = 2–2", "|score      = Cancelled"), "t")
    assert row["status"] == "CANC"


def test_round_labels_follow_the_api_per_cup():
    assert _round_label("Matches", "2017 Copa Sudamericana first stage", SUDAMERICANA) == "1st Round"
    assert _round_label("Second stage", "2015 Copa Sudamericana elimination stages", SUDAMERICANA) == "2nd Round"
    assert _round_label("Quarterfinals", "2014 Copa Libertadores knockout stage", LIBERTADORES) == "Quarter-finals"
    assert _round_label("First leg", "2014 Copa Sudamericana finals", SUDAMERICANA) == "Final"
    assert _round_label("Match details", "2014 Copa Libertadores finals", LIBERTADORES) == "Finals"
