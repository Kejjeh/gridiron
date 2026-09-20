"""Decision records surviving a runner that keeps nothing.

The cloud build starts from an empty checkout every time, and the decision
ledger is gitignored (rule #10 — the records name the owner's players), so
before this module every scheduled run was a FIRST run: "since the last
snapshot" had nothing to compare against and reported no change, and the
frozen page died with the container, which makes grading it next week
impossible.

The two-run test below is the acceptance evidence for that: run one freezes a
page and publishes it, run two starts from a genuinely empty ledger, restores,
and compares against run one. The rest pins the property that makes restoring
safe — a restored file is INPUT, and is validated before it is allowed to
count as history.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import carryover
from gridiron.decisions import archive_path, list_archives, read_archive, write_archive

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLI = _load("carry_dashboard_cli", "scripts/weekly/dashboard.py")
SCN = _load("carry_dashboard_scenarios", "scripts/weekly/dashboard_scenarios.py")
CARRY_CLI = _load("carry_cli", "scripts/cloud/carryover.py")


def _record(week: int = 3, when: datetime = NOW, **extra) -> dict:
    return {"week": week, "season": 2026,
            "generated": when.isoformat(timespec="seconds"), **extra}


def _store_one(store: Path, week: int, when: datetime, **extra) -> Path:
    rec = _record(week, when, **extra)
    return write_archive(rec, archive_path(2026, week, when, store, rec))


# ------------------------------------------------------- the two-run evidence

def _render(tmp_path: Path, tag: str, kind: str, now: datetime, ledger: Path) -> dict:
    root = tmp_path / f"cache_{tag}"
    SCN.build_scenario(root, kind, now=now)
    out = tmp_path / f"out_{tag}"
    rc = CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                   "--out-dir", str(out), "--archive-root", str(ledger),
                   "--now", now.isoformat()])
    assert rc == 0
    return json.loads((out / "dashboard_latest.json").read_text("utf-8"))


def test_two_ephemeral_runs_carry_one_history_between_them(tmp_path):
    """Run one freezes a page. Run two, on a runner with NOTHING on it,
    restores that page and compares against it."""
    store = tmp_path / "store"
    t1, t2 = SCN.NOW, SCN.NOW + timedelta(days=1)

    # ---- run one: no history exists yet, and the page says exactly that.
    ledger1 = tmp_path / "ledger1"
    first = carryover.restore(store, ledger1, season=2026, now=t1)
    assert first.accepted == () and "first run" in first.summary()
    rec1 = _render(tmp_path, "one", "complete", t1, ledger1)
    assert rec1["changes"] is None, "nothing to compare against on a first run"
    published = carryover.publish(ledger1, store, season=2026, now=t1)
    assert len(published.accepted) == 1

    # ---- run two: a brand new runner. The ledger starts empty.
    ledger2 = tmp_path / "ledger2"
    assert not ledger2.exists()
    restored = carryover.restore(store, ledger2, season=2026, now=t2)
    assert len(restored.accepted) == 1 and not restored.rejected
    assert "digest" in restored.accepted[0].reason, "provenance actually checked"

    rec2 = _render(tmp_path, "two", "stale", t2, ledger2)
    changes = rec2["changes"]
    assert changes is not None, "run two must see run one"
    assert changes["previous"] == rec1["generated"]
    assert changes["items"], "the two pages differ and the diff says how"
    assert any("freshness" in line for line in changes["items"])

    # ---- and run one's record is still there afterwards.
    carryover.publish(ledger2, store, season=2026, now=t2)
    stored = sorted(p.name for p in (store / "season2026").glob("*.json"))
    assert len(stored) == 2
    kept = {read_archive(p)["generated"] for _w, p in list_archives(store, 2026)}
    assert rec1["generated"] in kept and rec2["generated"] in kept


def test_a_failed_refresh_cannot_turn_last_good_into_current(tmp_path):
    """A run that freezes nothing must leave the chain exactly as it was. The
    danger is not losing the old record — it is the old record coming back
    wearing today's date, which would make a stale page the current one."""
    store = tmp_path / "store"
    _store_one(store, 3, NOW, current_points=111.0)

    later = NOW + timedelta(days=2)
    ledger = tmp_path / "ledger"
    carryover.restore(store, ledger, season=2026, now=later)
    before = {p.name: read_archive(p)["generated"] for _w, p in list_archives(ledger, 2026)}

    # the refresh failed: this run froze no page at all.
    report = carryover.publish(ledger, store, season=2026, now=later)

    after = {p.name: read_archive(p)["generated"] for _w, p in list_archives(ledger, 2026)}
    assert after == before, "no record was restamped"
    assert all(v.reason == "already stored; not rewritten" for v in report.accepted)
    assert max(after.values()) == NOW.isoformat(timespec="seconds")
    assert max(after.values()) != later.isoformat(timespec="seconds")


# --------------------------------------------------------------- provenance

def test_an_edited_record_is_refused_by_its_own_digest(tmp_path):
    store = tmp_path / "store"
    path = _store_one(store, 3, NOW, current_points=111.0)
    blob = json.loads(path.read_text("utf-8"))
    blob["current_points"] = 999.0
    path.write_text(json.dumps(blob, indent=1), encoding="utf-8")

    verdict = carryover.inspect_archive(path, season=2026, now=NOW + timedelta(days=1))
    assert not verdict.accepted and "digest" in verdict.reason
    report = carryover.restore(store, tmp_path / "ledger", season=2026,
                               now=NOW + timedelta(days=1))
    assert report.accepted == () and len(report.rejected) == 1
    assert not (tmp_path / "ledger" / "season2026").exists()


def test_a_renamed_record_is_refused_because_the_name_is_only_a_claim(tmp_path):
    store = tmp_path / "store"
    path = _store_one(store, 3, NOW)
    moved = path.with_name("week03_20261001T090000Z.json")
    path.rename(moved)
    verdict = carryover.inspect_archive(moved, season=2026, now=datetime(2026, 10, 2, tzinfo=UTC))
    assert not verdict.accepted and "the name is a claim" in verdict.reason


def test_a_record_stamped_in_the_future_is_refused(tmp_path):
    """The previous snapshot is chosen by time, so a future stamp is the one
    way a restored record could outrank the page being built."""
    store = tmp_path / "store"
    ahead = NOW + timedelta(days=5)
    _store_one(store, 3, ahead)
    report = carryover.restore(store, tmp_path / "ledger", season=2026, now=NOW)
    assert report.accepted == ()
    assert "in the future" in report.rejected[0].reason


def test_another_season_and_an_unknown_format_are_both_refused(tmp_path):
    store = tmp_path / "store"
    folder = store / "season2026"
    folder.mkdir(parents=True)
    wrong_season = {"archive_version": 1, "season": 2025, "week": 3,
                    "generated": NOW.isoformat(timespec="seconds")}
    (folder / "week03_20260926T120000Z.json").write_text(
        json.dumps(wrong_season), encoding="utf-8")
    from_future = {"archive_version": 99, "season": 2026, "week": 4,
                   "generated": NOW.isoformat(timespec="seconds")}
    (folder / "week04_20260926T120000Z.json").write_text(
        json.dumps(from_future), encoding="utf-8")

    report = carryover.restore(store, tmp_path / "ledger", season=2026,
                               now=NOW + timedelta(hours=1))
    reasons = " ".join(v.reason for v in report.rejected)
    assert report.accepted == ()
    assert "is not season 2026" in reasons and "newer than this code understands" in reasons


def test_a_restore_never_overwrites_a_record_this_run_already_wrote(tmp_path):
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    mine = _store_one(ledger, 3, NOW, current_points=1.0)
    theirs = store / "season2026" / mine.name
    theirs.parent.mkdir(parents=True)
    theirs.write_text(mine.read_text("utf-8").replace("1.0", "2.0"), encoding="utf-8")

    carryover.restore(store, ledger, season=2026, now=NOW + timedelta(hours=1))
    assert read_archive(mine)["current_points"] == 1.0


# ------------------------------------------------------------------- bounds

def test_the_carry_is_bounded_by_count_and_by_age(tmp_path):
    store = tmp_path / "store"
    for i in range(6):
        _store_one(store, 3, NOW - timedelta(days=i), n=i)
    _store_one(store, 2, NOW - timedelta(days=400), n="ancient")

    report = carryover.restore(store, tmp_path / "ledger", season=2026,
                               now=NOW + timedelta(hours=1), keep=3)
    assert len(report.accepted) == 3
    reasons = " ".join(v.reason for v in report.rejected)
    assert "carry limit" in reasons
    assert len(carryover.weeks_in(tmp_path / "ledger", 2026)) == 1


def test_publishing_prunes_the_store_it_inherited_to_the_keep_limit(tmp_path):
    """The store is a cache with a size limit, not an archive: a season of
    weekly runs must not grow in it without bound."""
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    for i in range(1, 5):                       # four older records already stored
        _store_one(store, 3, NOW - timedelta(days=i), n=i)
    _store_one(ledger, 3, NOW, n="today")       # this run froze one page

    report = carryover.publish(ledger, store, season=2026, now=NOW, keep=2)
    kept = sorted(p.name for p in (store / "season2026").glob("*.json"))
    assert len(kept) == 2, kept
    assert "pruned 3 record(s)" in report.summary()
    # the newest survives, the oldest does not
    newest = max(read_archive(p)["generated"] for _w, p in list_archives(store, 2026))
    assert newest == NOW.isoformat(timespec="seconds")


# ------------------------------------------------------------------- the CLI

def test_the_cli_reports_without_naming_a_player(tmp_path, capsys):
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    _store_one(store, 3, NOW, roster=[{"name": "Some Player", "sleeper_id": "x"}])
    rc = CARRY_CLI.main(["restore", "--store", str(store), "--ledger", str(ledger),
                         "--season", "2026", "--now", (NOW + timedelta(hours=1)).isoformat()])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Some Player" not in out
    assert "ledger holds week(s): 3" in out


def test_the_cli_can_be_told_to_fail_when_nothing_was_carried(tmp_path, capsys):
    rc = CARRY_CLI.main(["restore", "--store", str(tmp_path / "nope"),
                         "--ledger", str(tmp_path / "ledger"), "--season", "2026",
                         "--require"])
    assert rc == 1
    assert "carried nothing" in capsys.readouterr().err
