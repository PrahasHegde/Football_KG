"""The optional import scripts' parsing and matching rules, on small synthetic inputs (no network, no build)."""
from __future__ import annotations

import pandas as pd
import pytest

import fetch_club_data as clubs
import fetch_statsbomb_assists as sb
import fetch_wikipedia_goals as wiki


# ---------------------------------------------------------------- Wikipedia goal lists
def table(rows: list[str], header: str) -> str:
    return f"<table><tr>{header}</tr>{''.join(rows)}</table>"


MAIN = table(
    [f"<tr><td>{i}</td><td>{d}</td><td>{o}</td><td>Friendly</td></tr>"
     for i, (d, o) in enumerate([("1 June 2018", "Kenya"), ("4 June 2018", "Chinese Taipei"),
                                 ("7 June 2018", "Kenya"), ("10 June 2018", "New Zealand")], 1)],
    "<th>No.</th><th>Date</th><th>Opponent</th><th>Competition</th>")
HATTRICKS = table(["<tr><td>1</td><td>4 June 2018</td><td>Chinese Taipei</td><td>3</td></tr>"],
                  "<th>No.</th><th>Date</th><th>Opponent</th><th>Goals</th>")
YOUTH = table(["<tr><td>1</td><td>1 May 2005</td><td>Japan U15</td><td>Friendly</td></tr>"],
              "<th>No.</th><th>Date</th><th>Opponent</th><th>Competition</th>")


def test_goal_rows_reads_the_main_table_only():
    rows = wiki.goal_rows(MAIN + HATTRICKS + YOUTH)
    assert len(rows) == 4 and rows.goals.sum() == 4          # hat-trick + youth tables are not goals
    assert list(rows.opponent) == ["Kenya", "Chinese Taipei", "Kenya", "New Zealand"]
    assert rows.date.iloc[0] == pd.Timestamp("2018-06-01")


def test_goal_rows_drops_youth_opponents_and_sections():
    mixed = table(["<tr><td>Senior goals</td><td>Senior goals</td><td>Senior goals</td><td>Senior goals</td></tr>",
                   "<tr><td>1</td><td>1 June 2018</td><td>Kenya</td><td>Friendly</td></tr>",
                   "<tr><td>2</td><td>2 June 2018</td><td>Kenya U23</td><td>Friendly</td></tr>"],
                  "<th>No.</th><th>Date</th><th>Opponent</th><th>Competition</th>")
    rows = wiki.goal_rows(mixed)
    assert list(rows.opponent) == ["Kenya"]


def test_goal_rows_without_tables():
    assert wiki.goal_rows("<p>no tables</p>").empty


def test_norm():
    assert wiki.norm("Côte d'Ivoire [1]") == "cote d'ivoire"
    assert wiki.norm("  São  Tomé (PR) ") == "sao tome"


# ---------------------------------------------------------------- club linking
def test_name_key_ignores_order_and_hyphens():
    assert clubs.name_key("Heung-min Son") == clubs.name_key("Son Heung-min")
    assert clubs.name_key("Youssef El Arabi") == clubs.name_key("Youssef El-Arabi")
    assert clubs.name_key("Lionel Messi") != clubs.name_key("Lionel Scaloni")


@pytest.mark.parametrize("dob,first,last,median,ok", [
    ("1987-06-24", 2005, 2026, 2015, True),     # Messi
    ("1989-06-18", 1992, 2025, 2014, True),     # Aubameyang: one stray 1992 goal (his father) is tolerated
    ("1994-01-01", 2000, 2011, 2005, False),    # a namesake born too late for those goals
    ("1960-01-01", 2015, 2020, 2017, False),    # a namesake born too early
])
def test_plausible_uses_the_median_goal_year(dob, first, last, median, ok):
    assert clubs.plausible(dob, pd.Timestamp("2012-08-01"), pd.Timestamp("2020-01-01"), first, last, median) is ok


def test_plausible_without_a_birth_date_uses_the_club_career():
    s, e = pd.Timestamp("2013-01-01"), pd.Timestamp("2019-01-01")
    assert clubs.plausible(None, s, e, 2010, 2016, 2013)
    assert not clubs.plausible(None, s, e, 1980, 1990, 1985)
    assert not clubs.plausible(None, None, None, 2010, 2016, 2013)


# ---------------------------------------------------------------- StatsBomb minutes
@pytest.mark.parametrize("period,minute,expected", [(1, 0, 1), (1, 44, 45), (1, 47, 45), (2, 89, 90),
                                                    (2, 93, 90), (3, 100, 101), (4, 121, 120)])
def test_display_minute_caps_added_time(period, minute, expected):
    assert sb.display_minute(period, minute) == expected
