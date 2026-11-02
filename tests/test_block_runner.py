"""The block runner (scripts/research/block_runner.py) judges every roadmap
idea, so its scoring and its leak check are tested here on synthetic frames:
no research cache, no network."""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types

import numpy as np
import pandas as pd
import pytest

from gridiron.paths import REPO_ROOT

RESEARCH = REPO_ROOT / "scripts" / "research"


def _load(name):
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    spec = importlib.util.spec_from_file_location(name, RESEARCH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load("block_runner")


def _loop_pairwise(frame, systems):
    """projection_backtest.pairwise, copied as the reference."""
    hits = {s: 0 for s in systems}
    n = 0
    for _, g in frame.groupby(["week", "position"]):
        rows = g.to_dict("records")
        for a, b in itertools.combinations(rows, 2):
            if a["actual"] == b["actual"]:
                continue
            n += 1
            truth = a["actual"] > b["actual"]
            for s in systems:
                if a[s] != b[s] and (a[s] > b[s]) == truth:
                    hits[s] += 1
    return {"n": n, **{s: hits[s] / n for s in systems}}


def test_vectorised_pairwise_matches_the_reference_including_ties():
    rng = np.random.default_rng(7)
    n = 400
    df = pd.DataFrame({"week": rng.integers(4, 8, n), "position": rng.choice(["RB", "WR"], n),
                       "actual": rng.integers(0, 12, n).astype(float),   # many ties
                       "a": rng.normal(size=n).round(1), "b": rng.integers(0, 3, n).astype(float)})
    got, want = R.pairwise(df, ["a", "b"]), _loop_pairwise(df, ["a", "b"])
    assert got["n"] == want["n"]
    assert got["a"] == pytest.approx(want["a"]) and got["b"] == pytest.approx(want["b"])


def test_vectorised_ridge_predict_matches_the_row_loop():
    from gridiron.models import advanced as A
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"x": rng.normal(size=60), "y": rng.normal(size=60)})
    df.loc[::7, "y"] = np.nan
    m = A.Ridge.fit(df, df["x"] * 2 + 1, ["x", "y"])
    assert np.allclose(R.predict(m, df), m.predict(df))


def test_a_column_two_positions_share_gets_its_known_flag_for_both():
    t = pd.DataFrame({"season": 2020, "week": [4, 4, 5], "gsis_id": ["a", "b", "c"],
                      "position": ["WR", "TE", "TE"]})
    block = pd.DataFrame({"season": 2020, "week": [4, 4, 5], "gsis_id": ["a", "b", "c"],
                          "share": [0.3, np.nan, 0.1]})
    merged, cols = R.prepare(block, {2020: t}, {"WR": ["share"], "TE": ["share"]},
                             ["season", "week", "gsis_id"])
    assert cols == {"WR": ["share", "share__known"], "TE": ["share", "share__known"]}
    m = merged[2020]
    assert m["share"].tolist() == [0.3, 0.0, 0.1]          # 0 where missing, never a mean
    assert m["share__known"].tolist() == [1.0, 0.0, 1.0]


def _fake_context(results: pd.DataFrame):
    keys = pd.DataFrame({"season": [2021] * 2 + [2024] * 2, "week": [9, 9, 14, 14],
                         "team": ["AAA", "BBB", "AAA", "BBB"], "position": "DEF",
                         "gsis_id": ""})
    ctx = R.Context(keys, crosswalk=None, special_keys={"DEF": keys.drop(columns="gsis_id")})
    seasons = tuple(sorted(results["season"].unique()))
    ctx._store[("schedule_results", seasons, None)] = results
    return ctx


def _results():
    rows = []
    for season, last in ((2021, 9), (2024, 14)):
        for week in range(1, last + 2):
            rows.append({"season": season, "week": week, "team": "AAA", "pa": week * 1.0})
            rows.append({"season": season, "week": week, "team": "BBB", "pa": week * 2.0})
    return pd.DataFrame(rows)


def _module(include_this_week: bool, shuffle: bool):
    """An honest block sums the weeks BEFORE each target row (the shared
    as_of helper); the leaky one includes the target week's own result."""
    if str(R.BLOCKS_DIR) not in sys.path:
        sys.path.insert(0, str(R.BLOCKS_DIR))
    import _common as C

    def build(ctx):
        k = ctx.keys_for("DEF")[["season", "week", "team"]]
        r = ctx.schedule_results([2021, 2024])
        if include_this_week:
            r = r.sort_values(["season", "team", "week"])
            r["cum"] = r.groupby(["season", "team"])["pa"].cumsum()
            out = k.merge(r[["season", "week", "team", "cum"]], on=["season", "week", "team"],
                          how="left")
        else:
            out = C.as_of(k, r[["season", "week", "team", "pa"]], ["team"], ["pa"])
            out["cum"] = out["pa_std"]
        out = out[["season", "week", "team", "cum"]]
        return out.iloc[::-1] if shuffle else out
    return types.SimpleNamespace(ID="T-01", TITLE="t", COLUMNS={"DEF": ["cum"]}, build=build)


@pytest.mark.parametrize("shuffle", [False, True])
def test_leak_check_passes_an_honest_block_in_any_row_order(shuffle):
    ctx = _fake_context(_results())
    mod = _module(include_this_week=False, shuffle=shuffle)
    full, key = R.build_block(mod, ctx)
    assert R.leak_check(mod, ctx, full, key) == []


@pytest.mark.parametrize("shuffle", [False, True])
def test_leak_check_catches_this_weeks_result(shuffle):
    ctx = _fake_context(_results())
    mod = _module(include_this_week=True, shuffle=shuffle)
    full, key = R.build_block(mod, ctx)
    problems = R.leak_check(mod, ctx, full, key)
    assert len(problems) == 2 and all("differ" in p for p in problems)


def test_the_bar_needs_every_fold_on_both_metrics():
    def fold(pw_s, mae_s, pw_b, mae_b):
        return {"RB": {"shipped": {"pairwise": pw_s, "mae": mae_s},
                       "block": {"pairwise": pw_b, "mae": mae_b}},
                "pooled": {"shipped": {"pairwise": pw_s, "mae": mae_s},
                           "block": {"pairwise": pw_b, "mae": mae_b}}}
    good = {s: fold(0.6, 4.0, 0.61, 3.9) for s in range(2019, 2026)}
    assert R.verdict(good, ["RB"])["positions"]["RB"]["passes"]
    one_tie = dict(good)
    one_tie[2022] = fold(0.6, 4.0, 0.61, 4.0)              # MAE equal is not better
    v = R.verdict(one_tie, ["RB"])["positions"]["RB"]
    assert not v["passes"] and v["folds_better_on_both"] == 6


def test_team_codes_are_mapped_for_pandas_string_columns():
    df = pd.DataFrame({"home_team": pd.array(["OAK", "KC"], dtype="string"),
                       "away_team": ["SD", "STL"], "week": [1, 1]})
    out = R.normalize_teams(df.copy())
    assert out["home_team"].tolist() == ["LV", "KC"]
    assert out["away_team"].tolist() == ["LAC", "LA"]
