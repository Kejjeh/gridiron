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
from gridiron import ingest as ing
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
                         "--cache-root", str(tmp_path / "cache"), "--season", "2026",
                         "--now", (NOW + timedelta(hours=1)).isoformat()])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Some Player" not in out
    assert "ledger holds week(s): 3" in out


def test_the_cli_can_be_told_to_fail_when_nothing_was_carried(tmp_path, capsys):
    rc = CARRY_CLI.main(["restore", "--store", str(tmp_path / "nope"),
                         "--ledger", str(tmp_path / "ledger"), "--season", "2026",
                         "--cache-root", str(tmp_path / "cache"), "--require"])
    assert rc == 1
    # `--require` is about the decision RECORDS. An empty input carry is the
    # normal state of a run whose refresh worked, so it must never fail one.
    assert "carried no decision record" in capsys.readouterr().err


# ------------------------------------------- carrying the INPUTS, not just the
# conclusions. The records alone left a hole: a clean runner whose refresh fails
# has no cache, so it cannot rebuild ANY page — the render exited 2, the run
# produced nothing, and the summary said "Built." These run the real drivers
# (the carryover CLI and the dashboard CLI) over two isolated runner
# directories, because the hole was in the drivers, not in the copy helper.

def _runner(tmp_path: Path, tag: str) -> tuple[Path, Path, Path]:
    """One ephemeral runner: its own cache root, ledger and output dir."""
    base = tmp_path / tag
    cache, ledger, out = base / "cache", base / "ledger", base / "out"
    for d in (cache, ledger, out):
        d.mkdir(parents=True, exist_ok=True)
    return cache, ledger, out


def _carry(action: str, store: Path, cache: Path, ledger: Path, now: datetime) -> int:
    return CARRY_CLI.main([action, "--store", str(store), "--ledger", str(ledger),
                           "--cache-root", str(cache), "--season", "2026",
                           "--now", now.isoformat()])


def _render_cli(cache: Path, ledger: Path, out: Path, now: datetime) -> int:
    return CLI.main(["--cache-root", str(cache), "--owner", "fixture_owner",
                     "--write", "--out-dir", str(out), "--archive-root", str(ledger),
                     "--now", now.isoformat()])


def test_a_second_runner_with_every_refresh_failed_still_renders_last_known(tmp_path):
    """The acceptance case, end to end, through the actual command lines.

    Run one: refreshes succeed, a page is built, records AND inputs are
    published. Run two: a different directory with an empty cache, standing in
    for a clean hosted runner on which every refresh failed. It must still
    produce a usable page that dates its evidence to run one's pull, withholds
    every action, and leaves run one's frozen record untouched.
    """
    store = tmp_path / "store"
    t1 = SCN.NOW
    t2 = t1 + timedelta(hours=6)

    # ---- run one -----------------------------------------------------------
    cache1, ledger1, out1 = _runner(tmp_path, "run1")
    SCN.build_scenario(cache1, "complete", now=t1)
    assert _carry("restore", store, cache1, ledger1, t1) == 0
    assert _render_cli(cache1, ledger1, out1, t1) == 0
    assert _carry("publish", store, cache1, ledger1, t1) == 0

    rec1 = json.loads((out1 / "dashboard_latest.json").read_text("utf-8"))
    frozen = sorted((store / "season2026").glob("*.json"))
    assert len(frozen) == 1
    before = frozen[0].read_bytes()
    assert (store / carryover.INPUTS_DIR / "season2026" / "manifest.json").is_file()

    # ---- run two: nothing on disk, and no refresh will fill it --------------
    cache2, ledger2, out2 = _runner(tmp_path, "run2")
    assert not list(cache2.rglob("*.json")), "the second runner starts empty"
    # Before the input carry existed, this render returned 2 and wrote nothing.
    assert _render_cli(cache2, ledger2, out2, t2) == 2

    assert _carry("restore", store, cache2, ledger2, t2) == 0
    assert _render_cli(cache2, ledger2, out2, t2) == 0, \
        "a failed refresh must still produce a page from last-good inputs"

    rec2 = json.loads((out2 / "dashboard_latest.json").read_text("utf-8"))
    # Honest: the page is degraded, dated to run one, and recommends nothing.
    assert rec2["degraded"] is True
    assert rec2["actionable"] == 0
    assert set(rec2["withheld_actions"]) == {"lineup", "waiver", "matchup"}
    assert all("CARRIED FORWARD" in line for line in rec2["sources"])
    for line in rec2["sources"]:
        assert t1.strftime("%Y-%m-%d") in line, \
            "every source is dated to the pull that actually fetched it"
    # Usable: it can still compare itself against the page run one froze.
    assert rec2["changes"] is not None
    assert rec2["changes"]["previous"] == rec1["generated"]
    # No card gives an instruction.
    for action in rec2["actions"]:
        text = f"{action['title']} {action['body']}".lower()
        for imperative in ("consider claiming", "add ", "start ", "drop ", "fill "):
            assert not text.startswith(imperative), action["title"]

    # ---- and run one's record is byte-for-byte what it was -----------------
    assert frozen[0].read_bytes() == before


def test_a_carried_input_never_displaces_one_this_run_pulled(tmp_path):
    """A refresh that WORKED outranks anything in the store, always. The
    carry is a floor under a failed run, never a source of truth."""
    store = tmp_path / "store"
    t1, t2 = SCN.NOW, SCN.NOW + timedelta(hours=6)
    cache1, ledger1, _out1 = _runner(tmp_path, "src")
    SCN.build_scenario(cache1, "stale", now=t1)
    carryover.publish_inputs(ing.season_cache(2026, cache1), store,
                             season=2026, now=t1)

    cache2, _l2, _o2 = _runner(tmp_path, "fresh")
    SCN.build_scenario(cache2, "complete", now=t2)
    live = ing.Manifest.load(ing.season_cache(2026, cache2), 2026)
    before = {n: e.as_of for n, e in live.entries.items()}

    report = carryover.restore_inputs(store, ing.season_cache(2026, cache2),
                                      season=2026, now=t2)
    after = ing.Manifest.load(ing.season_cache(2026, cache2), 2026)
    assert before == {n: e.as_of for n, e in after.entries.items()}
    assert not any(e.error == carryover.CARRIED_FORWARD for e in after.entries.values())
    assert report.accepted == ()
    assert all("already has its own copy" in v.reason for v in report.rejected)


def test_inputs_too_old_to_render_from_are_refused(tmp_path):
    """Records stay useful for weeks; a roster does not. Past the input limit
    the carry declines rather than rendering a board from last month."""
    store = tmp_path / "store"
    cache1, _l, _o = _runner(tmp_path, "old")
    SCN.build_scenario(cache1, "complete", now=SCN.NOW)
    carryover.publish_inputs(ing.season_cache(2026, cache1), store,
                             season=2026, now=SCN.NOW)

    much_later = SCN.NOW + timedelta(days=carryover.INPUT_MAX_AGE_DAYS + 2)
    cache2, _l2, _o2 = _runner(tmp_path, "empty")
    report = carryover.restore_inputs(store, ing.season_cache(2026, cache2),
                                      season=2026, now=much_later)
    assert report.accepted == ()
    assert "past the" in report.rejected[0].reason
    assert not list((ing.season_cache(2026, cache2)).glob("*"))


def test_inputs_stamped_in_the_future_are_refused(tmp_path):
    """The same rule the records live under: a carried file may never present
    itself as newer than the run reading it."""
    store = tmp_path / "store"
    cache1, _l, _o = _runner(tmp_path, "ahead")
    SCN.build_scenario(cache1, "complete", now=SCN.NOW)
    carryover.publish_inputs(ing.season_cache(2026, cache1), store,
                             season=2026, now=SCN.NOW)

    verdict, blob = carryover.inspect_inputs(store, season=2026,
                                             now=SCN.NOW - timedelta(days=2))
    assert not verdict.accepted and "future" in verdict.reason
    assert blob == {}


def test_another_seasons_inputs_are_not_this_seasons_history(tmp_path):
    store = tmp_path / "store"
    cache1, _l, _o = _runner(tmp_path, "s2026")
    SCN.build_scenario(cache1, "complete", now=SCN.NOW)
    carryover.publish_inputs(ing.season_cache(2026, cache1), store,
                             season=2026, now=SCN.NOW)
    # The store now holds season 2026 under its own folder; asking for 2025
    # must find nothing rather than reading 2026's files as 2025's.
    verdict, _blob = carryover.inspect_inputs(store, season=2025, now=SCN.NOW)
    assert not verdict.accepted

    # And a manifest whose contents disagree with the folder it sits in.
    index = store / carryover.INPUTS_DIR / "season2026" / "manifest.json"
    blob = json.loads(index.read_text("utf-8"))
    blob["season"] = "not a year"
    index.write_text(json.dumps(blob), encoding="utf-8")
    verdict, _blob = carryover.inspect_inputs(store, season=2026, now=SCN.NOW)
    assert not verdict.accepted and "not season" in verdict.reason


# --------------------------------------------- a malformed record is REJECTED,
# not a crash. `int(blob.get("season") or 0)` raised ValueError on a record
# whose season was the string "invalid": the validator handed the malformed
# file the power to stop the whole restore, which is precisely the outcome
# validation exists to prevent. Every field read out of a restored record is
# now read totally, and one bad file costs only itself.

def _write_raw(store: Path, name: str, blob: object) -> Path:
    folder = store / "season2026"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(blob, default=str), encoding="utf-8")
    return path


def test_a_record_whose_season_is_not_a_year_is_refused_not_raised(tmp_path):
    bad = _write_raw(tmp_path / "store", "week03_20260926T120000Z.json",
                     {"archive_version": 1, "season": "invalid", "week": 3,
                      "generated": NOW.isoformat(timespec="seconds")})
    verdict = carryover.inspect_archive(bad, season=2026, now=NOW)
    assert not verdict.accepted
    assert "is not a year" in verdict.reason


def test_a_season_argument_that_is_not_a_year_is_refused_not_raised(tmp_path):
    """The caller can be wrong too, and a bad argument must not be answered
    with a confident accept."""
    good = _store_one(tmp_path / "store", 3, NOW)
    verdict = carryover.inspect_archive(good, season="invalid", now=NOW)
    assert not verdict.accepted
    assert "not a year" in verdict.reason


def test_wrong_types_and_nulls_are_each_refused_on_their_own_terms(tmp_path):
    """Every shape a hand-edited or half-written record can take. None of
    these may raise, and each must name what is actually wrong with it."""
    store = tmp_path / "store"
    stamp = NOW.isoformat(timespec="seconds")
    cases = {
        "week03_20260926T120000Z.json": (
            {"archive_version": 1, "season": None, "week": 3, "generated": stamp},
            "season"),
        "week04_20260926T120000Z.json": (
            {"archive_version": 1, "season": [2026], "week": 4, "generated": stamp},
            "season"),
        "week05_20260926T120000Z.json": (
            {"archive_version": 1, "season": 2026, "week": None, "generated": stamp},
            "week"),
        "week06_20260926T120000Z.json": (
            {"archive_version": 1, "season": 2026, "week": {"n": 6},
             "generated": stamp}, "week"),
        "week07_20260926T120000Z.json": (
            {"archive_version": 1, "season": 2026, "week": 7, "generated": None},
            "generated"),
        "week08_20260926T120000Z.json": (
            {"archive_version": "one", "season": 2026, "week": 8, "generated": stamp},
            "archive_version"),
        "week09_20260926T120000Z.json": ([1, 2, 3], "not an object"),
        "week10_20260926T120000Z.json": ("a string", "not an object"),
    }
    for name, (blob, expected) in cases.items():
        path = _write_raw(store, name, blob)
        verdict = carryover.inspect_archive(path, season=2026, now=NOW)
        assert not verdict.accepted, name
        assert expected in verdict.reason, (name, verdict.reason)
    # `True` is an int in Python. A flag is not a season.
    flag = _write_raw(store, "week11_20260926T120000Z.json",
                      {"archive_version": 1, "season": True, "week": 11,
                       "generated": stamp})
    assert not carryover.inspect_archive(flag, season=2026, now=NOW).accepted


def test_a_record_filed_under_the_wrong_week_is_refused(tmp_path):
    """The digest covers the CONTENTS, so it cannot catch a name that
    disagrees with them. A record filed under week 3 that holds week 9 would
    be picked as week 3's previous snapshot and diffed against the wrong page.
    """
    store = tmp_path / "store"
    rec = _record(9, NOW)
    honest = write_archive(rec, archive_path(2026, 9, NOW, store, rec))
    assert carryover.inspect_archive(honest, season=2026, now=NOW).accepted

    misfiled = honest.parent / honest.name.replace("week09", "week03")
    misfiled.write_bytes(honest.read_bytes())
    verdict = carryover.inspect_archive(misfiled, season=2026, now=NOW)
    assert not verdict.accepted
    assert "week 3" in verdict.reason and "week 9" in verdict.reason


def test_one_malformed_record_does_not_cost_the_sound_one_beside_it(tmp_path):
    """The defect that mattered: a single unreadable field aborted `restore`,
    so a run that had a perfectly good previous page carried nothing at all.
    """
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    good = _store_one(store, 4, NOW, current_points=101.0)
    _write_raw(store, "week03_20260926T120000Z.json",
               {"archive_version": 1, "season": "invalid", "week": 3,
                "generated": NOW.isoformat(timespec="seconds")})
    _write_raw(store, "week02_20260926T120000Z.json", {"nothing": "useful"})

    report = carryover.restore(store, ledger, season=2026,
                               now=NOW + timedelta(hours=1))
    assert len(report.accepted) == 1
    assert len(report.rejected) == 2
    assert (ledger / "season2026" / good.name).is_file()
    assert carryover.weeks_in(ledger, 2026) == (4,)
    # Every refusal says what was wrong; none of them says "crashed".
    assert all(v.reason for v in report.rejected)


# ------------------------------------------- a malformed carried INPUT entry
# is rejected ON ITS OWN, and never before its type is checked.
#
# `ing.Entry(**raw)` looked like validation and was not. A dataclass checks
# which KEYS it was handed and never what they hold, so a carried entry with
# `path: ["bad"]` constructed cleanly and then blew up at `Path(entry.path)`,
# taking down the restore of every sound source beside it. The quieter half
# was worse: `rows: "many"` and `as_of: "not-a-time"` survived carryover
# entirely and raised inside `Manifest.freshness` half way through a render,
# long after the carry had reported success.

GOOD_INPUT = {"name": "sleeper_state", "path": "sleeper_state.json", "rows": 1,
              "as_of": "2026-09-20T12:00:00+00:00", "source": "sleeper"}


def _carried(store: Path, entries: dict, *, files=("sleeper_state.json",),
             season: int = 2026) -> Path:
    """A private store holding a carried cache, written by hand so the test
    controls exactly what a previous run is claimed to have left behind."""
    d = store / carryover.INPUTS_DIR / f"season{season}"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("{}", encoding="utf-8")
    (d / ing.MANIFEST_NAME).write_text(
        json.dumps({"season": season, "entries": entries}), encoding="utf-8")
    return d


def _restore_inputs(tmp_path: Path, entries: dict, *, files=("sleeper_state.json",),
                    now: datetime | None = None):
    store, cache = tmp_path / "store", tmp_path / "cache"
    _carried(store, entries, files=files)
    when = now or datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)
    report = carryover.restore_inputs(store, cache, season=2026, now=when)
    return report, cache


def _malformed(**kw) -> dict:
    return {"name": "weekly_stats", "path": "weekly_stats.parquet", "rows": 1,
            "as_of": "2026-09-20T12:00:00+00:00", "source": "synthetic", **kw}


BOTH_FILES = ("sleeper_state.json", "weekly_stats.parquet")


def test_a_carried_path_that_is_not_a_string_is_refused_not_raised(tmp_path):
    """The reported defect, exactly: `path: ["bad"]` passed `inspect_inputs`
    and then raised TypeError out of `Path()`."""
    report, cache = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path=["bad"]),
                   "sleeper_state": dict(GOOD_INPUT)})
    assert [v.name for v in report.rejected] == ["weekly_stats"]
    assert "not a plain filename" in report.rejected[0].reason
    # The sound sibling beside it survived, laid down and readable.
    assert [v.name for v in report.accepted] == ["sleeper_state.json"]
    assert (cache / "sleeper_state.json").is_file()
    assert set(ing.Manifest.load(cache, 2026).entries) == {"sleeper_state"}


def test_a_carried_path_that_is_a_mapping_is_refused_too(tmp_path):
    report, _ = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path={"p": "x"}),
                   "sleeper_state": dict(GOOD_INPUT)})
    assert [v.name for v in report.rejected] == ["weekly_stats"]
    assert len(report.accepted) == 1


def test_an_absolute_carried_path_cannot_reach_outside_the_cache(tmp_path):
    """`dir / "/etc/passwd"` is `/etc/passwd` — pathlib DISCARDS the left
    operand. Before this, an absolute carried path was silently basenamed for
    the copy but written into the manifest VERBATIM, so `Manifest.file()`
    handed the renderer a file that never travelled with the carry and that
    nothing here had validated."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "weekly_stats.parquet").write_text("NOT FROM THE STORE",
                                                  encoding="utf-8")
    report, cache = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path=str(outside / "weekly_stats.parquet")),
                   "sleeper_state": dict(GOOD_INPUT)},
        files=BOTH_FILES)
    assert [v.name for v in report.rejected] == ["weekly_stats"]
    manifest = ing.Manifest.load(cache, 2026)
    assert "weekly_stats" not in manifest.entries
    # Nothing the renderer can reach lies outside the cache it was given.
    for name in manifest.entries:
        assert manifest.file(name).parent == cache


def test_a_traversing_carried_path_is_refused_rather_than_basenamed(tmp_path):
    """Taking `.name` off `../../x` does not fix it — it accepts the entry
    while silently changing which file it means."""
    report, cache = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path="../../weekly_stats.parquet"),
                   "sleeper_state": dict(GOOD_INPUT)},
        files=BOTH_FILES)
    assert [v.name for v in report.rejected] == ["weekly_stats"]
    assert not (cache / "weekly_stats.parquet").exists()


def test_carried_fields_the_renderer_reads_are_checked_before_construction(tmp_path):
    """Each of these once survived the carry and raised DURING a render.

    `rows` is compared against an int by `freshness.assess`; `as_of` is parsed
    by `Entry.as_of_dt`; `weeks` is iterated as ints. A validator that lets a
    bad value through to a place that raises is not a validator.
    """
    cases = {
        "rows is not a number": _malformed(rows="many"),
        "rows is a flag": _malformed(rows=True),
        "rows is negative": _malformed(rows=-3),
        "as_of is unparseable": _malformed(as_of="not-a-time"),
        "as_of is null": _malformed(as_of=None),
        "weeks is a string": _malformed(weeks="3"),
        "weeks holds a non-week": _malformed(weeks=[1, "two"]),
        "source is null": _malformed(source=None),
        "missing_columns is not a list": _malformed(missing_columns="wopr"),
        "the entry is not an object": ["not", "an", "entry"],
        "the entry carries an unknown field": _malformed(sneaked="in"),
    }
    for label, bad in cases.items():
        report, cache = _restore_inputs(
            tmp_path / label.replace(" ", "_"),
            {"weekly_stats": bad, "sleeper_state": dict(GOOD_INPUT)},
            files=BOTH_FILES)
        assert [v.name for v in report.rejected] == ["weekly_stats"], label
        assert report.rejected[0].reason, label
        # ...and the sound sibling is carried, every time.
        assert [v.name for v in report.accepted] == ["sleeper_state.json"], label
        manifest = ing.Manifest.load(cache, 2026)
        assert set(manifest.entries) == {"sleeper_state"}, label
        # Whatever was rejected, what IS here still renders without raising.
        for name in manifest.entries:
            assert manifest.freshness(name, now=SCN.NOW).refresh_failed, label


def test_a_surviving_carried_entry_keeps_its_original_as_of_and_still_withholds(tmp_path):
    """The two properties a rejection must not be allowed to erode."""
    report, cache = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path=["bad"]),
                   "sleeper_state": dict(GOOD_INPUT)})
    assert len(report.accepted) == 1
    entry = ing.Manifest.load(cache, 2026).entries["sleeper_state"]
    assert entry.as_of == GOOD_INPUT["as_of"]          # verbatim, not restated
    assert entry.error == carryover.CARRIED_FORWARD
    fresh = ing.Manifest.load(cache, 2026).freshness("sleeper_state", now=SCN.NOW)
    assert fresh.refresh_failed and fresh.status is not ing.Status.FRESH


def test_a_publish_whose_manifest_points_at_a_bad_path_still_stores_the_rest(tmp_path):
    """The same hole on the publish side: `_cache_files` called `Path()` on
    every entry's `path`, so one bad value stopped the whole publish."""
    cache, store = tmp_path / "cache", tmp_path / "store"
    cache.mkdir()
    (cache / "sleeper_state.json").write_text("{}", encoding="utf-8")
    (cache / ing.MANIFEST_NAME).write_text(json.dumps(
        {"season": 2026, "entries": {"weekly_stats": _malformed(path=["bad"]),
                                     "sleeper_state": dict(GOOD_INPUT)}}),
        encoding="utf-8")
    report = carryover.publish_inputs(cache, store, season=2026, now=SCN.NOW)
    stored = store / carryover.INPUTS_DIR / "season2026"
    assert (stored / "sleeper_state.json").is_file()
    assert (stored / ing.MANIFEST_NAME).is_file()
    assert any("not a plain filename" in v.reason for v in report.rejected)


def test_a_cache_manifest_this_run_cannot_read_stops_the_carry_rather_than_guessing(tmp_path):
    """The local manifest is how a carry knows what this run already pulled.
    Reading it as "nothing" is exactly how a carried file comes to displace a
    real one, so an unreadable one fails closed."""
    store, cache = tmp_path / "store", tmp_path / "cache"
    _carried(store, {"sleeper_state": dict(GOOD_INPUT)})
    cache.mkdir()
    (cache / ing.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    report = carryover.restore_inputs(store, cache, season=2026, now=SCN.NOW)
    assert not report.accepted
    assert "already pulled" in report.rejected[0].reason
    publish = carryover.publish_inputs(cache, tmp_path / "s2", season=2026,
                                       now=SCN.NOW)
    assert not publish.accepted and "could not be read" in publish.note


# --------------------------------------- a carried file never displaces one
# that is already here — decided by DESTINATION, not by entry name.
#
# The name check was not the same check. A carried `weekly_stats` pointing at
# `shared.json` and a local `sleeper_state` pointing at the same `shared.json`
# share no name, so the carry copied over the local file — and the LOCAL entry
# was left describing bytes it had never seen, with `error` empty and a current
# `as_of`, so the gate did not withhold on it either.

def _local_cache(cache: Path, name: str, filename: str, body: str,
                 as_of: str = "2026-09-20T13:00:00+00:00") -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / filename).write_text(body, encoding="utf-8")
    ing.Manifest(cache, {name: ing.Entry(name=name, path=filename, rows=1,
                                         as_of=as_of, source="synthetic")},
                 2026).save()


def _entry(name: str, path: str, as_of: str = "2026-09-20T12:00:00+00:00") -> dict:
    return {"name": name, "path": path, "rows": 1, "as_of": as_of,
            "source": "synthetic"}


LATER = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)


def test_a_carried_file_never_displaces_a_local_one_under_another_name(tmp_path):
    store, cache = tmp_path / "store", tmp_path / "cache"
    _local_cache(cache, "sleeper_state", "shared.json", "CURRENT")
    before = (cache / "shared.json").read_bytes()
    meta_before = json.loads((cache / ing.MANIFEST_NAME).read_text(
        encoding="utf-8"))["entries"]["sleeper_state"]

    _carried(store, {"weekly_stats": _entry("weekly_stats", "shared.json"),
                     "injuries": _entry("injuries", "injuries.parquet")},
             files=("shared.json", "injuries.parquet"))
    (store / carryover.INPUTS_DIR / "season2026" / "shared.json").write_text(
        "OLD", encoding="utf-8")

    report = carryover.restore_inputs(store, cache, season=2026, now=LATER)

    # The local file and its metadata are untouched, byte for byte.
    assert (cache / "shared.json").read_bytes() == before
    meta_after = json.loads((cache / ing.MANIFEST_NAME).read_text(
        encoding="utf-8"))["entries"]["sleeper_state"]
    assert meta_after == meta_before
    # The collision is reported, naming the local owner it would have hit.
    assert [v.name for v in report.rejected] == ["shared.json"]
    assert "sleeper_state" in report.rejected[0].reason
    # ...and the sound, independent carried sibling still comes across.
    assert (cache / "injuries.parquet").is_file()
    assert [v.name for v in report.accepted] == ["injuries.parquet"]
    assert set(ing.Manifest.load(cache, 2026).entries) == {"sleeper_state", "injuries"}


def test_a_carried_file_cannot_displace_a_local_one_through_a_case_alias(tmp_path):
    """The store is written by one machine and read by another — a Windows
    desktop publishing and a Linux runner restoring is the normal path — so a
    case alias is refused on every platform, not only the ones where the
    filesystem would collide."""
    store, cache = tmp_path / "store", tmp_path / "cache"
    _local_cache(cache, "sleeper_state", "shared.json", "CURRENT")
    _carried(store, {"weekly_stats": _entry("weekly_stats", "Shared.JSON")},
             files=("Shared.JSON",))
    report = carryover.restore_inputs(store, cache, season=2026, now=LATER)
    assert (cache / "shared.json").read_text(encoding="utf-8") == "CURRENT"
    assert not report.accepted
    assert "already holds" in report.rejected[0].reason


def test_two_carried_entries_naming_one_file_do_not_overwrite_each_other(tmp_path):
    """Nothing here can tell whether two sources that name the same file mean
    the same bytes, and a carry may never be the thing that finds out."""
    store, cache = tmp_path / "store", tmp_path / "cache"
    _carried(store, {"aaa_first": _entry("aaa_first", "shared.json"),
                     "zzz_second": _entry("zzz_second", "shared.json")},
             files=("shared.json",))
    report = carryover.restore_inputs(store, cache, season=2026, now=LATER)
    assert [v.name for v in report.accepted] == ["shared.json"]
    assert set(ing.Manifest.load(cache, 2026).entries) == {"aaa_first"}
    assert "already holds" in report.rejected[0].reason


def test_a_carried_entry_cannot_overwrite_the_manifest_it_is_read_from(tmp_path):
    store, cache = tmp_path / "store", tmp_path / "cache"
    _local_cache(cache, "sleeper_state", "sleeper_state.json", "CURRENT")
    _carried(store, {"weekly_stats": _entry("weekly_stats", ing.MANIFEST_NAME)},
             files=("sleeper_state.json",))
    index = json.loads((cache / ing.MANIFEST_NAME).read_text(encoding="utf-8"))
    report = carryover.restore_inputs(store, cache, season=2026, now=LATER)
    assert json.loads((cache / ing.MANIFEST_NAME).read_text(
        encoding="utf-8")) == index
    assert not report.accepted


def test_a_padded_carried_path_is_refused_rather_than_trimmed(tmp_path):
    """`" weekly_stats.parquet "` names a different file from
    `"weekly_stats.parquet"`. Trimming it is the same defect as basenaming an
    absolute path: the entry is accepted and what it means is changed."""
    assert carryover._safe_basename(" weekly_stats.parquet ") is None
    assert carryover._safe_basename("weekly_stats.parquet ") is None
    assert carryover._safe_basename("\tweekly_stats.parquet") is None
    assert carryover._safe_basename("weekly_stats.parquet") == "weekly_stats.parquet"
    report, cache = _restore_inputs(
        tmp_path, {"weekly_stats": _malformed(path=" weekly_stats.parquet "),
                   "sleeper_state": dict(GOOD_INPUT)}, files=BOTH_FILES)
    assert [v.name for v in report.rejected] == ["weekly_stats"]
    assert [v.name for v in report.accepted] == ["sleeper_state.json"]
