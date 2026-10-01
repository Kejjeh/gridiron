"""Offline draft-pipeline regressions, using the saved local draft inputs."""
from collections import Counter
import runpy

import numpy as np
import pandas as pd
import pytest

from gridiron.paths import REPO_ROOT, RESEARCH_CACHE


@pytest.fixture(scope="module")
def pipeline():
    if not (RESEARCH_CACHE / "draft2026" / "sleeper_projections_2026.csv").exists():
        pytest.skip("cached draft inputs required for integration tests")
    return runpy.run_path(str(REPO_ROOT / "scripts/research/draft_board_2026.py"),
                          run_name="draft_pipeline_test")


@pytest.mark.parametrize("policy", ["static_vor", "dynamic_vona", "rb_rb_rb",
                                   "rb_wr_wr", "rb_rb_wr", "rb_te_wr",
                                   "rb_wr_qb", "wr_at_1"])
def test_all_policies_complete_legal_rosters(pipeline, policy):
    _, rosters, _ = pipeline["run_sim"](policy, 5)
    for roster in rosters:
        assert len(roster) == len(set(roster)) == 15
        counts = Counter(pipeline["pos_arr"][i] for i in roster)
        assert all(counts[p] >= n for p, n in
                   [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("DEF", 1)])
        assert sum(counts[p] for p in ["RB", "WR", "TE"]) >= 7


def test_policies_use_reproducible_random_draws(pipeline):
    a = pipeline["run_sim"]("static_vor", 2)
    pipeline["run_sim"]("rb_wr_wr", 2)
    b = pipeline["run_sim"]("static_vor", 2)
    assert a[1] == b[1]
    assert np.array_equal(a[0], b[0])


def test_season_exclusions_are_not_revived_by_ecr(pipeline):
    b = pipeline["board"]
    excluded = b[b.name_key.isin(["jaydenhiggins", "rickypearsall", "isiahpacheco"])]
    assert len(excluded) > 0
    assert excluded.proj.eq(0).all()
    assert excluded.draft_excluded.all()


def test_all_user_picks_have_adp_predictions(pipeline):
    assert all(f"p{k}" in pipeline["board"] for k in pipeline["MY_PICKS"])

