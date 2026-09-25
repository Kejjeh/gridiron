"""A carried input that this run did not refresh is not a FAILED refresh.

Production 2026-09-25 (runs 36074980438 and 36095609134, RECOVERY NEEDED):
the pull made NO player-map request — the budget withheld it and said so —
yet the board's banner read "designations … STALE (refresh FAILED)" and every
reason line "REFRESH FAILED at <restore time>: CARRIED FORWARD from an earlier
run; this run did not refresh it". Nothing failed; nothing was attempted. The
owner's need is a person's bootstrap, not a retry of a broken Sleeper call.

These tests pin the wording and, just as hard, that the gates do not move:
a carried entry still reads not-FRESH (`refresh_failed` stays the gate's
flag), keeps its original as-of, and withholds every action resting on it.
A real failure still says REFRESH FAILED.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import carryover, gating, ingest, sleeper
from gridiron.freshness import SourceFreshness, Status
from gridiron.ingest import Manifest

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 9, 25, 4, 44, tzinfo=UTC)
RESTORED = "2026-09-25T04:43:49+00:00"


def _load_script(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _carried(tmp_path: Path, pulled: datetime) -> Manifest:
    m = Manifest(tmp_path, {}, 2026)
    good = tmp_path / "sleeper_players.json"
    good.write_text(json.dumps({"1": {}}), encoding="utf-8")
    m.record("sleeper_players", path=good, rows=1, source="t", as_of=pulled)
    m.entries["sleeper_players"] = replace(m.get("sleeper_players"),
                                           error=carryover.CARRIED_FORWARD,
                                           last_attempt=RESTORED)
    return m


def test_the_carried_mark_has_one_spelling():
    assert carryover.CARRIED_FORWARD == ingest.CARRIED_FORWARD


def test_a_carried_entry_reads_not_refreshed_never_refresh_failed(tmp_path):
    pulled = NOW - timedelta(hours=11)
    f = _carried(tmp_path, pulled).freshness("sleeper_players", now=NOW)
    assert "REFRESH FAILED" not in f.reason
    assert carryover.CARRIED_FORWARD in f.reason and RESTORED in f.reason
    assert f.carried
    # the gate's flag and the status are unchanged: it can never read FRESH
    assert f.refresh_failed and f.status is Status.STALE
    assert f.as_of == pulled


def test_a_carried_entry_inside_its_limit_is_still_not_fresh(tmp_path):
    f = _carried(tmp_path, NOW - timedelta(hours=1)).freshness("sleeper_players", now=NOW)
    assert f.status is Status.STALE and f.refresh_failed and f.carried


def test_a_real_failure_still_says_refresh_failed(tmp_path):
    m = _carried(tmp_path, NOW - timedelta(hours=11))
    m.record_failure("sleeper_players", source="t", error="ReadTimeout: 60s", at=NOW)
    f = m.freshness("sleeper_players", now=NOW)
    assert "REFRESH FAILED at" in f.reason and "ReadTimeout" in f.reason
    assert f.refresh_failed and not f.carried


def test_a_carried_box_score_frame_is_still_blocked_without_claiming_a_failure():
    s = SourceFreshness("weekly_stats", Status.STALE, NOW, 10, 2,
                        f"pulled 1h ago; {carryover.CARRIED_FORWARD}",
                        covered_weeks=(1, 2), refresh_failed=True, carried=True)
    blockers = gating.box_score_blockers([s], evidence_boundary=2)
    text = " ".join(r for _, r in blockers["lineup"])
    assert blockers["lineup"] and "not refreshed by this run" in text
    assert "refresh FAILED" not in text
    failed = replace(s, carried=False)
    assert "the latest refresh FAILED" in gating.box_score_blockers(
        [failed], evidence_boundary=2)["lineup"][0][1]


def test_the_recovery_board_says_not_refreshed_and_still_withholds(tmp_path):
    """The production shape: no ledger, the map carried and not requested."""
    scn = _load_script("carried_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load_script("carried_cli", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    directory = root / "season2026"
    m = Manifest.load(directory, 2026)
    e = m.get("sleeper_players")
    m.entries["sleeper_players"] = replace(e, error=carryover.CARRIED_FORWARD,
                                           last_attempt=scn.NOW.isoformat())
    m.save()
    b = sleeper.player_map_budget(sleeper.PlayerMapHistory(problem="no request ledger"),
                                  scn.NOW)
    sleeper.write_player_map_status(directory, scn.NOW, b)
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat()])
    html = (out / "dashboard_latest.html").read_text("utf-8")
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    assert "REFRESH FAILED" not in html and "refresh FAILED" not in html
    assert "(not refreshed)" in html and "RECOVERY NEEDED" in html
    assert "DEGRADED" in html and rec["degraded"]
    # the gates are exactly as before: the moves resting on designations wait
    assert any("lineup actions WITHHELD" in n and "sleeper_players" in n for n in rec["notes"])
    assert any("waiver actions WITHHELD" in n and "sleeper_players" in n for n in rec["notes"])
    assert e.as_of in json.dumps(rec) or e.as_of[:16].replace("T", " ") in html
