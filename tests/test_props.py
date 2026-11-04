"""The prop-market module (`gridiron.props`): parse, de-vig, consensus,
expected stats, identity and the market projection.

Fictional players, fictional books and fictional gsis ids (00-0099xxx) only.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from gridiron import props as P
from gridiron.league_config import DEFAULT_SCORING, ScoringRules
from gridiron.scoring import fantasy_points, kicker_points

UTC = timezone.utc
KICK = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
TAKEN = KICK - timedelta(minutes=110)
HOME, AWAY = "Kansas City Chiefs", "Buffalo Bills"      # KC, BUF
NFLVERSE_32 = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
               "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO",
               "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"}


def ou(player, over, under, point):
    return [{"name": "Over", "description": player, "price": over, "point": point},
            {"name": "Under", "description": player, "price": under, "point": point}]


def event(books, *, home=HOME, away=AWAY, event_id="evt1"):
    return {"id": event_id, "sport_key": "americanfootball_nfl",
            "commence_time": KICK.isoformat().replace("+00:00", "Z"),
            "home_team": home, "away_team": away,
            "bookmakers": [{"key": key, "title": key.upper(), "markets": [
                {"key": mk, "last_update": "2026-11-08T16:00:00Z", "outcomes": outs}
                for mk, outs in markets.items()]} for key, markets in books.items()]}


def table(*rows):
    return P.IdentityTable(P.IdentityRow(*r) for r in rows)


QB, WR, KK = "00-0099001", "00-0099002", "00-0099003"
TABLE = table(("Pat Fixture", "KC", QB, "exact", "2026-11-02"),
              ("Wes Fixture", "BUF", WR, "reviewed", "2026-11-02"),
              ("Kip Fixture", "KC", KK, "exact", "2026-11-02"))
PARAMS = P.PropParams.load()


def snapshot():
    return event({
        "book_alpha": {
            "player_pass_yds": ou("Pat Fixture", -115, -105, 245.5),
            "player_pass_tds": ou("Pat Fixture", 120, -150, 1.5),
            "player_rush_yds": ou("Pat Fixture", -110, -110, 18.5),
            "player_reception_yds": ou("Wes Fixture", -110, -110, 61.5),
            "player_receptions": ou("Wes Fixture", -130, 100, 4.5) + ou("Nobody Fixture", -110, -110, 2.5),
            "player_anytime_td": [{"name": "Yes", "description": "Pat Fixture", "price": 400},
                                  {"name": "Yes", "description": "Wes Fixture", "price": 150},
                                  {"name": "No", "description": "Wes Fixture", "price": -200}],
            "player_kicking_points": ou("Kip Fixture", -105, -115, 7.5)},
        "book_beta": {
            "player_pass_yds": ou("Pat Fixture", -110, -110, 249.5),
            "player_reception_yds": ou("Wes Fixture", -105, -115, 62.5),
            "player_anytime_td": [{"name": "Yes", "description": "Pat Fixture", "price": 350}]},
        "book_gamma": {   # Yes-only and no two-way prop to de-vig it with
            "player_anytime_td": [{"name": "Yes", "description": "Pat Fixture", "price": 300}]},
    })


# ------------------------------------------------------------- constants

def test_the_team_map_has_exactly_the_32_nflverse_abbreviations():
    assert len(P.TEAM_ABBR) == 32
    assert set(P.TEAM_ABBR.values()) == NFLVERSE_32
    assert P.NFL_TEAMS == frozenset(NFLVERSE_32)


def test_the_budgeted_markets_are_catalogued_and_core_costs_at_most_six_credits():
    assert len(P.CORE_MARKETS) * len(P.REGIONS) == 6
    for key in (*P.CORE_MARKETS, *P.SECONDARY_MARKETS):
        assert key in P.MARKETS
    assert not set(P.CORE_MARKETS) & set(P.SECONDARY_MARKETS)


# ------------------------------------------------------------- distributions

def test_the_pure_python_distributions_match_scipy():
    sp = pytest.importorskip("scipy.special")
    st = pytest.importorskip("scipy.stats")
    for a, x in ((0.7, 0.3), (2.0, 1.1), (13.9, 12.0), (40.0, 55.0), (1.6, 7.0)):
        assert P.gamma_p(a, x) == pytest.approx(sp.gammainc(a, x), abs=1e-12)
        assert P.gamma_q(a, x) == pytest.approx(sp.gammaincc(a, x), abs=1e-12)
    for a, p in ((1.6, 0.3), (2.0, 0.5), (13.9, 0.62)):
        assert P.gamma_q_inverse(a, p) == pytest.approx(sp.gammainccinv(a, p), rel=1e-10)
    for n, mu, r in ((4, 4.6, None), (4, 4.6, 10.7), (33, 34.0, 23.0), (0, 0.8, None)):
        ref = st.poisson.cdf(n, mu) if r is None else st.nbinom.cdf(n, r, r / (r + mu))
        assert P.count_cdf(n, mu, r) == pytest.approx(ref, abs=1e-12)


def test_the_solved_mean_reproduces_the_market_probability():
    for line, p, k in ((245.5, 0.52, 13.9), (61.5, 0.47, 1.63), (7.5, 0.5, 5.0)):
        mean = P.gamma_mean(line, p, k)
        assert P.gamma_q(k, k * line / mean) == pytest.approx(p, abs=1e-10)
        assert mean > line          # a right-skewed stat's mean sits above its median
    for line, p, r in ((4.5, 0.55, 10.7), (1.5, 0.45, None), (34.5, 0.48, 23.0)):
        assert P.count_over(line, P.count_mean(line, p, r), r) == pytest.approx(p, abs=1e-9)


def test_a_whole_number_line_takes_the_push_out():
    mu = 4.0
    pushless = (1 - P.count_cdf(4, mu, None)) / (1 - P.count_pmf(4, mu, None))
    assert P.count_over(4.0, mu, None) == pytest.approx(pushless)
    assert P.count_mean(4.0, 0.5, None) > 4.0 - 1e-9


def test_the_anytime_td_is_minus_log_of_no_touchdown():
    assert P.anytime_lambda(0.4) == pytest.approx(-math.log(0.6))
    value, why = P.expected_stat("player_anytime_td", None, 0.4, PARAMS)
    assert why == "" and value == pytest.approx(-math.log(0.6))


def test_an_implausible_main_line_abstains_rather_than_extrapolating():
    for market, line, p in (("player_pass_yds", 245.5, 0.99), ("player_receptions", 4.5, 0.01),
                            ("player_rush_yds", 0.0, 0.5), ("player_pass_yds", None, 0.5)):
        value, why = P.expected_stat(market, line, p, PARAMS)
        assert value is None and why


# ------------------------------------------------------------- fitted parameters

def test_the_committed_parameters_cover_every_modelled_market_with_evidence():
    blob = json.loads(P.PARAMS_PATH.read_text("utf-8"))
    assert blob["meta"]["seasons"] == list(range(2019, 2026))
    assert blob["meta"]["script"] == "scripts/research/prop_distribution_fit.py"
    for m in P.MARKETS.values():
        if m.family == P.GAMMA:
            assert m.stat in PARAMS.gamma_shape, m.stat
            ev = blob["gamma"][m.stat]
            assert ev["n"] > 1000 and len(ev["held_out"]) == 7
            assert max(abs(v) for v in ev["band_quantile_error"].values()) <= 0.06
        elif m.family == P.COUNT and m.stat != "pat_made":
            assert m.stat in PARAMS.count_size, m.stat
    td = blob["anytime_td"]
    assert td["family"] == "poisson"
    for q in td["by_expected_td_quintile"].values():
        assert abs(q["stated"] - q["observed"]) < 0.02


# ------------------------------------------------------------- parse / de-vig

def test_american_prices_become_implied_probabilities():
    assert P.implied_probability(150) == pytest.approx(0.4)
    assert P.implied_probability(-150) == pytest.approx(0.6)
    assert P.implied_probability(100) == P.implied_probability(-100) == 0.5
    with pytest.raises(ValueError):
        P.implied_probability(50)


def test_rows_keep_the_name_exactly_as_sent_and_count_what_they_skip():
    payload = event({"book_alpha": {
        "player_receptions": ou("Sam Fixture Jr. ", -110, -110, 3.5)
        + [{"name": "Over", "price": -110, "point": 3.5}],        # no player
        "h2h": [{"name": HOME, "price": -150}]}})
    rows, skipped = P.parse_event_odds(payload)
    assert {r.player for r in rows} == {"Sam Fixture Jr. "}        # never stripped
    assert {(r.home, r.away) for r in rows} == {("KC", "BUF")}
    assert skipped["outcome without a player or side"] == 1
    assert skipped["market not catalogued"] == 1
    assert P.parse_event_odds(["not", "an", "event"])[1]["response is not an event object"] == 1


def test_two_way_markets_are_normalised_multiplicatively():
    lines, _ = P.devig(P.parse_event_odds(event({"book_alpha": {
        "player_pass_yds": ou("Pat Fixture", -110, -110, 245.5),
        "player_receptions": ou("Wes Fixture", -120, 100, 4.5)}}))[0])
    by = {b.market: b for b in lines}
    assert by["player_pass_yds"].p_over == pytest.approx(0.5)
    assert by["player_pass_yds"].overround == pytest.approx(2 * 110 / 210 - 1)
    qo, qu = 120 / 220, 0.5
    assert by["player_receptions"].p_over == pytest.approx(qo / (qo + qu))


def test_the_main_line_is_the_one_nearest_even_and_one_sided_pairs_are_dropped():
    rows, _ = P.parse_event_odds(event({"book_alpha": {
        "player_reception_yds": ou("Wes Fixture", -110, -110, 61.5) + ou("Wes Fixture", -400, 300, 40.5)
        + [{"name": "Over", "description": "Dee Fixture", "price": -110, "point": 30.5}]}}))
    lines, dropped = P.devig(rows)
    assert [(b.player, b.point) for b in lines] == [("Wes Fixture", 61.5)]
    assert dropped["alternate line set aside (main line kept)"] == 1
    assert dropped["over/under not offered as one clean pair"] == 1


def test_a_yes_only_anytime_td_uses_the_books_own_median_overround():
    lines, dropped = P.devig(P.parse_event_odds(snapshot())[0])
    alpha = [b.overround for b in lines if b.book == "book_alpha" and b.overround is not None]
    yes = next(b for b in lines if b.book == "book_alpha" and b.player == "Pat Fixture"
               and b.market == "player_anytime_td")
    assert yes.method == "one-sided"
    # every two-way pair of the book, main lines and the Yes/No pair alike
    assert yes.p_over == pytest.approx((100 / 500) / (1 + sorted(alpha)[len(alpha) // 2]), rel=0.05)
    beta = next(b for b in lines if b.book == "book_beta" and b.market == "player_anytime_td")
    assert beta.method == "one-sided"
    assert not any(b.book == "book_gamma" for b in lines)
    assert dropped["anytime TD Yes only, book has no two-way prop to de-vig it"] == 1


def test_the_consensus_is_the_median_across_books():
    cons = P.consensus(P.devig(P.parse_event_odds(snapshot())[0])[0])
    c = cons[("evt1", "Pat Fixture", "player_pass_yds")]
    assert c.books == 2 and c.line == pytest.approx(247.5)
    assert c.p_over == pytest.approx((0.5 + (115 / 215) / (115 / 215 + 105 / 205)) / 2)


# ------------------------------------------------------------- identity

def test_the_committed_table_reads_and_is_valid():
    t = P.IdentityTable.read()
    assert all(r.basis in P.BASES for r in t.rows())


def test_a_name_resolves_only_through_the_table_and_otherwise_abstains():
    assert TABLE.resolve("Pat Fixture", ("KC", "BUF")).gsis_id == QB
    assert TABLE.resolve("Pat Fixture", ("NYJ", "BUF")).reason == "unmatched"   # wrong teams
    assert TABLE.resolve("Pat  Fixture", ("KC", "BUF")).reason == "unmatched"   # no fuzzing
    assert TABLE.resolve("pat fixture", ("KC", "BUF")).reason == "unmatched"
    two = table(("Lee Fixture", "KC", "00-0099010", "exact", "2026-11-02"),
                ("Lee Fixture", "BUF", "00-0099011", "exact", "2026-11-02"))
    assert two.resolve("Lee Fixture", ("KC", "BUF")).reason == "ambiguous"
    assert two.resolve("Lee Fixture", ("KC", "NYJ")).gsis_id == "00-0099010"


@pytest.mark.parametrize("row", [
    ("Pat Fixture", "KAN", QB, "exact", "2026-11-02"),      # not an nflverse team
    ("Pat Fixture", "KC", "12345", "exact", "2026-11-02"),  # not a gsis id
    ("Pat Fixture", "KC", QB, "fuzzy", "2026-11-02"),       # not a basis
    ("Pat Fixture", "KC", QB, "exact", "Nov 2"),            # not a date
    ("", "KC", QB, "exact", "2026-11-02"),
])
def test_a_malformed_table_row_is_refused(row):
    with pytest.raises(ValueError):
        table(row)


def test_one_key_mapped_to_two_players_is_refused():
    with pytest.raises(ValueError):
        table(("Pat Fixture", "KC", QB, "exact", "2026-11-02"),
              ("Pat Fixture", "KC", WR, "reviewed", "2026-11-02"))


def test_serving_refuses_any_row_no_person_has_reviewed():
    with pytest.raises(P.UnreviewedIdentity):
        TABLE.serving_id("Pat Fixture", ("KC", "BUF"))
    assert TABLE.serving_id("Wes Fixture", ("KC", "BUF")) == WR
    with pytest.raises(LookupError):
        TABLE.serving_id("Nobody Fixture", ("KC", "BUF"))
    # a reviewed duplicate of an exact row upgrades it
    both = table(("Pat Fixture", "KC", QB, "exact", "2026-11-02"),
                 ("Pat Fixture", "KC", QB, "reviewed", "2026-11-03"))
    assert both.serving_id("Pat Fixture", ("KC", "BUF")) == QB


def test_the_table_file_must_carry_the_exact_header(tmp_path):
    bad = tmp_path / "names.csv"
    bad.write_text("name,team,gsis_id,basis,added\n", encoding="utf-8")
    with pytest.raises(ValueError):
        P.IdentityTable.read(bad)


# ------------------------------------------------------------- derived entry

def test_the_derived_entry_is_ids_and_expected_stats_only():
    entry = P.derive_game(snapshot(), table=TABLE, params=PARAMS, taken_at=TAKEN, kickoff=KICK)
    assert set(entry["players"]) == {QB, WR, KK}
    assert entry["unmatched"] == 1 and entry["ambiguous"] == 0       # counted, never named
    assert entry["lead_minutes"] == 110.0 and entry["teams"] == ["BUF", "KC"]
    qb = entry["players"][QB]
    assert qb["basis"] == "exact" and qb["books"]["passing_yards"] == 2
    assert set(qb["stats"]) == {"passing_yards", "passing_tds", "rushing_yards", "scrimmage_tds"}
    assert entry["players"][WR]["td_method"] == "two-way"
    text = json.dumps(entry)
    for leak in ("Fixture", "book_", "245.5", "249.5", "-115", "evt1", "BOOK"):
        assert leak not in text, f"the public entry leaked {leak!r}"


def test_two_odds_names_for_one_player_set_his_numbers_aside():
    t = table(("Pat Fixture", "KC", QB, "exact", "2026-11-02"),
              ("P. Fixture", "KC", QB, "reviewed", "2026-11-02"))
    payload = event({"book_alpha": {"player_pass_yds": ou("Pat Fixture", -110, -110, 245.5)
                                    + ou("P. Fixture", -110, -110, 230.5)}})
    e = P.derive_game(payload, table=t, params=PARAMS, taken_at=TAKEN, kickoff=KICK)
    assert e["players"][QB]["stats"] == {}
    assert "all" in e["players"][QB]["abstained"]


def test_a_yes_only_td_no_book_could_de_vig_is_said_on_the_player():
    payload = event({
        "book_alpha": {"player_pass_yds": ou("Pat Fixture", -110, -110, 245.5)},
        "book_gamma": {   # Yes only, and this book prices nothing two-way here
            "player_pass_yds": [{"name": "Over", "description": "Pat Fixture", "price": -110,
                                 "point": 245.5}],
            "player_anytime_td": [{"name": "Yes", "description": "Pat Fixture", "price": 300}]}})
    e = P.derive_game(payload, table=TABLE, params=PARAMS, taken_at=TAKEN, kickoff=KICK)
    qb = e["players"][QB]
    assert "passing_yards" in qb["stats"] and "scrimmage_tds" not in qb["stats"]
    assert "de-vig" in qb["abstained"]["scrimmage_tds"]
    # and the projection does not swap in his own touchdown rate for the market's
    res = P.market_projection(qb, "QB", _rates(rushing_tds=0.4))
    assert res.points is None and "scrimmage_tds" in res.reason


# ------------------------------------------------------------- market projection

def _rates(**over):
    base = {c: 0.0 for c in P.RATE_COLUMNS}
    base.update(over)
    return base


def test_the_market_projection_is_the_league_score_of_the_expected_line():
    entry = P.derive_game(snapshot(), table=TABLE, params=PARAMS, taken_at=TAKEN,
                          kickoff=KICK)["players"][QB]
    rates = _rates(passing_interceptions=0.8, rushing_fumbles_lost=0.1, receptions=0.1,
                   receiving_yards=0.5, passing_2pt_conversions=0.05, rushing_tds=0.3)
    res = P.market_projection(entry, "QB", rates)
    s = entry["stats"]
    expected = {**rates, "passing_yards": s["passing_yards"], "passing_tds": s["passing_tds"],
                "rushing_yards": s["rushing_yards"]}
    ry, cy = s["rushing_yards"], rates["receiving_yards"]
    expected["rushing_tds"] = s["scrimmage_tds"] * ry / (ry + cy)
    expected["receiving_tds"] = s["scrimmage_tds"] * cy / (ry + cy)
    assert res.points == pytest.approx(fantasy_points(expected, DEFAULT_SCORING))
    assert "passing_interceptions" in res.rate_stats and "rushing_fumbles_lost" in res.rate_stats
    assert "rushing_tds" not in res.rate_stats            # the market's TDs replaced the rate
    assert res.books == 2


def test_the_touchdown_split_moves_no_point_while_both_touchdowns_score_six():
    entry = {"stats": {"receptions": 5.0, "receiving_yards": 60.0, "scrimmage_tds": 0.5},
             "books": {"receptions": 3}}
    a = P.market_projection(entry, "WR", _rates(rushing_yards=4.0))
    b = P.market_projection(entry, "WR", _rates(rushing_yards=40.0))
    tds = 0.5 * 6.0
    assert a.points == pytest.approx(5 * 0.5 + 60 * 0.1 + 4.0 * 0.1 + tds)
    assert b.points - a.points == pytest.approx(3.6)     # the rushing yards, not the split
    skewed = ScoringRules(rush_td=4.0)
    assert P.market_projection(entry, "WR", _rates(rushing_yards=40.0), rules=skewed).points \
        != pytest.approx(b.points)


def test_a_receivers_missing_market_and_his_rushing_come_from_his_own_rate():
    entry = {"stats": {"receptions": 5.0}, "books": {"receptions": 2}}
    res = P.market_projection(entry, "WR", _rates(receiving_yards=55.0, rushing_yards=3.0))
    assert res.points == pytest.approx(5 * 0.5 + 55 * 0.1 + 3 * 0.1)
    assert set(res.rate_stats) == {"receiving_yards", "rushing_yards"}
    assert res.market_stats == ("receptions",)


@pytest.mark.parametrize("entry,pos,rates,why", [
    ({"stats": {"receptions": 5.0}}, "QB", _rates(), "position-core"),
    ({"stats": {"passing_yards": 250.0}}, "RB", _rates(), "position-core"),
    ({"stats": {"rushing_yards": 60.0}}, "WR", _rates(), "position-core"),
    ({"stats": {"passing_yards": 250.0}}, "QB", None, "no game this season"),
    ({"stats": {"passing_yards": 250.0}, "abstained": {"passing_tds": "x"}}, "QB", _rates(),
     "could not be read"),
    ({"stats": {"passing_yards": 250.0}}, "DST", _rates(), "position"),
])
def test_the_projection_abstains_rather_than_guess(entry, pos, rates, why):
    res = P.market_projection(entry, pos, rates)
    assert res.points is None and why in res.reason


def test_a_kickers_market_points_are_split_by_his_own_season():
    rates = _rates(fg_made_30_39=1.0, fg_made_40_49=0.5, fg_made_50_59=0.5, pat_made=3.0,
                   fg_missed=0.25, pat_missed=0.1)
    res = P.market_projection({"stats": {"kicking_points": 12.0}, "books": {}}, "K", rates)
    book = 3 * 2.0 + 3.0                                  # 9 book points a game
    scale = 12.0 / book
    line = {"fg_made_30_39": scale, "fg_made_40_49": 0.5 * scale, "fg_made_50_59": 0.5 * scale,
            "pat_made": 3.0 * scale, "fg_missed": 0.25, "pat_missed": 0.1}
    assert res.points == pytest.approx(kicker_points(line))
    none = P.market_projection({"stats": {"kicking_points": 8.0}}, "K", _rates())
    assert none.points is None and "split" in none.reason


def test_season_rates_are_per_game_and_never_read_ahead():
    frame = pd.DataFrame([
        {"gsis_id": QB, "week": 1, "passing_yards": 200.0, "rushing_fumbles_lost": 1.0},
        {"gsis_id": QB, "week": 2, "passing_yards": 300.0, "rushing_fumbles_lost": None},
        {"gsis_id": QB, "week": 9, "passing_yards": 999.0, "rushing_fumbles_lost": 5.0},
        {"gsis_id": WR, "week": 9, "passing_yards": 0.0, "rushing_fumbles_lost": 0.0}])
    r = P.season_rates(frame, through_week=8)
    assert r[QB]["passing_yards"] == pytest.approx(250.0)
    assert r[QB]["rushing_fumbles_lost"] == pytest.approx(0.5)
    assert WR not in r                                   # no game yet: UNKNOWN, not zero
    assert P.season_rates(frame, through_week=None) == {}


class _Pl:
    def __init__(self, sid, gid, pos, kickoff):
        self.sleeper_id, self.gsis_id, self.position, self.kickoff = sid, gid, pos, kickoff


def _week(entry_fn=None, taken=TAKEN):
    e = P.derive_game(snapshot(), table=TABLE, params=PARAMS, taken_at=taken, kickoff=KICK)
    blob = P.empty_week(2026, 9)
    blob["games"]["2026_09_BUF_KC"] = e
    return P.MarketWeek(blob)


def test_a_number_needs_a_snapshot_taken_before_his_kickoff():
    wk = _week()
    rates = {QB: _rates(), WR: _rates(rushing_yards=2.0)}
    nums, detail = P.market_block(wk, [_Pl("11", QB, "QB", KICK), _Pl("12", WR, "WR", KICK),
                                       _Pl("13", "00-0099099", "RB", KICK)], rates)
    assert set(nums) == {"11", "12"} and detail["no_market"] == 1
    assert detail["identity_basis"] == {"exact": 1, "reviewed": 1}
    assert detail["from_rates"] == {"12": ["rushing_yards"]}
    late = _week(taken=KICK + timedelta(minutes=1))
    nums, detail = P.market_block(late, [_Pl("11", QB, "QB", KICK)], rates)
    assert nums == {} and detail["abstained"] == {"snapshot not taken before his kickoff": 1}
    nums, detail = P.market_block(wk, [_Pl("11", QB, "QB", None)], rates)
    assert nums == {} and "kickoff or snapshot time unknown" in detail["abstained"]
    assert P.market_block(None, [_Pl("11", QB, "QB", KICK)], rates) == ({}, {})


def test_a_player_in_two_games_snapshots_abstains():
    e = P.derive_game(snapshot(), table=TABLE, params=PARAMS, taken_at=TAKEN, kickoff=KICK)
    blob = P.empty_week(2026, 9)
    blob["games"]["2026_09_BUF_KC"] = e
    blob["games"]["2026_09_XXX_YYY"] = e
    wk = P.MarketWeek(blob)
    assert wk.project(QB, "QB", KICK, _rates()).reason == "in two games' snapshots this week"


def test_the_derived_file_is_read_only_for_its_own_season_and_week(tmp_path):
    blob = P.empty_week(2026, 9)
    path = P.derived_path(tmp_path, 2026, 9)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert P.read_derived(tmp_path, 2026, 9) == blob
    assert P.read_derived(tmp_path, 2026, 10) is None
    path.write_text(json.dumps({**blob, "week": 8}), encoding="utf-8")
    assert P.read_derived(tmp_path, 2026, 9) is None
    path.write_text("{not json", encoding="utf-8")
    assert P.read_derived(tmp_path, 2026, 9) is None
