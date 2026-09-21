"""The Free Agent Radar's archive block and its like-for-like diff.

The radar is `gridiron.waivers.build_board` written down for every projected
player in the pool (see `WaiverBoard.candidates`). This module does two
things with it and nothing else:

  1. `radar_record` turns the board into the JSON block the decision-time
     archive carries under `"radar"`, keyed by stable Sleeper ids (rule #3),
     so a later page can say what changed WITHOUT recomputing anything.
  2. `diff_radar` compares two such blocks and reports transitions only
     (rule #8): a player newly available, a player now owned by a roster, a
     verdict that moved, a projection that moved by more than the noise
     threshold, a source whose evidence expired. It separates a REFRESH (the
     inputs carry a newer timestamp) from a REVISION (a number or a verdict
     actually changed), because "the snapshot is 15 minutes newer" and
     "something is different" are two different facts and the page has to
     say which one it has.

What it refuses to invent: a comparison across weeks (a week rollover means
no valid comparison, and the block says so), a comparison against a record
that predates the radar, a comparison for the first record of a season, and
any movement in a player it cannot find on both sides. Nothing here is a
projection, a price or a recommendation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timezone

from gridiron.decisions import MOVE_POINTS
from gridiron.freshness import SourceFreshness
from gridiron.ids import normalize_id
from gridiron.lineup import Player
from gridiron.waivers import LINEUP, Candidate, WaiverBoard

#: Bumped when the block's shape changes. A reader treats an unknown
#: version as "not comparable", never as "close enough".
RADAR_VERSION = 1

#: How many players a change list names before it says "and N more".
NAMED_CAP = 12


def _player(p: Player | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.sleeper_id, "name": p.name, "position": p.position, "team": p.team,
            "projected": p.projection.mean, "lineup": p.lineup}


def candidate_record(c: Candidate, designation: str = "") -> dict:
    """One radar row, by id. Every number on it is THIS WEEK's.

    Both ids are copied from the Player as the board built it; the Sleeper
    to gsis resolution happened upstream through `gridiron.ids.Crosswalk`
    (rule #3) and nothing here maps one id onto the other."""
    pr = c.add.projection
    return {
        "id": c.add.sleeper_id, "gsis_id": c.add.gsis_id, "name": c.add.name,
        "position": c.add.position, "team": c.add.team,
        "projected": pr.mean, "sd": pr.sd, "withheld": pr.is_withheld,
        "model_mean": pr.model_mean, "reasons": list(pr.reasons),
        "designation": designation,
        "locked": c.add.locked, "lock_known": c.add.lock_known,
        "lock_note": c.add.lock_note,
        "kickoff": (c.add.kickoff.astimezone(timezone.utc).isoformat(timespec="seconds")
                    if c.add.kickoff else None),
        "verdict": c.verdict, "reason": c.reason,
        "lineup_gain": c.lineup_gain if c.verdict == LINEUP else None,
        "slot": c.slot, "drop": _player(c.drop), "displaces": _player(c.displaces),
        "alternatives": [{"drop": _player(d), "lineup_gain": g} for d, g in c.alternatives],
        "versus": _player(c.versus), "gap": c.gap,
    }


def radar_record(board: WaiverBoard, *, pool: Sequence[Player],
                 owned_ids: Iterable[str], snapshot_as_of: str,
                 sources: Sequence[SourceFreshness],
                 designations: Mapping[str, str] | None = None) -> dict:
    """The archive block. `pool` is every available player (projected or
    not); `owned_ids` every id on any roster in the snapshot, so a later
    diff can tell "claimed" from "no longer eligible"."""
    designations = designations or {}
    evidence = {s.name: (s.as_of.astimezone(timezone.utc).isoformat(timespec="seconds")
                         if s.as_of else None) for s in sources}
    status = {s.name: s.status.value for s in sources}
    cands = board.candidates
    return {
        "version": RADAR_VERSION,
        "snapshot_as_of": snapshot_as_of,
        "evidence": evidence,
        "status": status,
        "abstained": board.abstained,
        "counts": {
            "pool": board.pool_size,
            "projected": sum(1 for p in pool if p.projected),
            "evaluated": board.evaluated,
            "unprojected": board.unprojected,
            "lineup": sum(1 for c in cands if c.verdict == LINEUP),
            "listed": len(cands),
        },
        "positions": [pc.record() for pc in board.positions],
        "pool": [{"id": p.sleeper_id, "name": p.name, "position": p.position,
                  "team": p.team, "projected": p.projection.mean}
                 for p in sorted(pool, key=lambda p: p.sleeper_id)],
        "owned": sorted({normalize_id(i) for i in owned_ids if normalize_id(i)}),
        "candidates": [candidate_record(c, designations.get(c.add.sleeper_id, ""))
                       for c in cands],
    }


# --------------------------------------------------------------------------
# Since the last record: transitions only, like for like
# --------------------------------------------------------------------------
#: Change kinds that mean a NUMBER or a VERDICT moved, as opposed to the
#: pool's membership or a source's timestamp.
REVISION_KINDS = frozenset({"verdict", "projection"})


@dataclass(frozen=True)
class RadarChange:
    kind: str          # available | owned | gone | verdict | projection | evidence
    subject: str       # a player name (or a source name), display only
    detail: str
    player_id: str = ""

    def line(self) -> str:
        return f"{self.kind}: {self.subject} — {self.detail}"


@dataclass(frozen=True)
class RadarChanges:
    """What moved between two radar blocks, and whether that is a real
    comparison at all."""

    comparable: bool
    why: str                                  # why there is no comparison, or ""
    previous: str                             # the previous record's generated stamp
    previous_week: int | None
    #: Inputs whose as-of moved, one line each. A refresh with no revision is
    #: the normal 15-minute case and is said exactly that way.
    refreshed: tuple[str, ...] = field(default=())
    items: tuple[RadarChange, ...] = field(default=())

    @property
    def revised(self) -> bool:
        return any(c.kind in REVISION_KINDS for c in self.items)

    @property
    def any(self) -> bool:
        return bool(self.items)

    def of(self, kind: str) -> tuple[RadarChange, ...]:
        return tuple(c for c in self.items if c.kind == kind)

    def summary(self) -> str:
        """One sentence for the top of the page."""
        if not self.comparable:
            return f"No comparison: {self.why}."
        n = len(self.items)
        rev = sum(1 for c in self.items if c.kind in REVISION_KINDS)
        pool = sum(1 for c in self.items if c.kind in ("available", "owned", "gone"))
        ev = sum(1 for c in self.items if c.kind == "evidence")
        if not n and not self.refreshed:
            return (f"Unchanged: the same inputs as the record of {self.previous}; "
                    f"nothing was refreshed and nothing moved.")
        if not n:
            return (f"Refreshed, not revised: {'; '.join(self.refreshed)} — the numbers, "
                    f"verdicts and pool are identical to the record of {self.previous}.")
        bits = []
        if pool:
            bits.append(f"{pool} pool change(s)")
        if rev:
            bits.append(f"{rev} revision(s) to a projection or verdict")
        if ev:
            bits.append(f"{ev} evidence change(s)")
        head = ("Refreshed and revised" if self.refreshed else
                "Revised on the same input timestamps")
        return f"{head} since {self.previous}: " + ", ".join(bits) + "."

    def record(self) -> dict:
        return {"comparable": self.comparable, "why": self.why, "previous": self.previous,
                "previous_week": self.previous_week, "refreshed": list(self.refreshed),
                "revised": self.revised, "summary": self.summary(),
                "items": [{"kind": c.kind, "subject": c.subject, "detail": c.detail,
                           "player_id": c.player_id} for c in self.items]}


def _block(record: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if not isinstance(record, Mapping):
        return None
    r = record.get("radar")
    return r if isinstance(r, Mapping) else None


def _index(rows: object, key: str = "id") -> dict[str, Mapping[str, object]]:
    out: dict[str, Mapping[str, object]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping) and row.get(key) is not None:
            out[normalize_id(row.get(key))] = row
    return out


def _stamp(value: object) -> str:
    text = str(value or "")
    return text.replace("T", " ")[:16] + (" UTC" if text else "")


def _names(rows: Sequence[Mapping[str, object]]) -> str:
    names = [f"{r.get('name') or 'sleeper:' + str(r.get('id'))} ({r.get('position') or '?'})"
             for r in rows[:NAMED_CAP]]
    more = len(rows) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def diff_radar(previous: Mapping[str, object] | None,
               current: Mapping[str, object]) -> RadarChanges:
    """Transitions between the radar blocks of two frozen records.

    Reads both as data and computes nothing that needed a projection. When
    the two records are not comparable the result says why, and carries no
    items at all — an empty list would read as "nothing changed".
    """
    cur = _block(current)
    prev_stamp = str((previous or {}).get("generated") or "unknown") if previous else ""
    prev_week = (previous.get("week") if previous and isinstance(previous.get("week"), int)
                 else None)
    if previous is None:
        return RadarChanges(False, "no earlier record this season — a first run has nothing "
                            "to compare against, and no movement is invented", "", None)
    prev = _block(previous)
    if cur is None:
        return RadarChanges(False, "this record carries no radar block", prev_stamp, prev_week)
    if prev is None:
        return RadarChanges(False, f"the previous record ({_stamp(prev_stamp)}) predates the "
                            f"radar and lists no pool, so there is nothing like for like "
                            f"to compare", prev_stamp, prev_week)
    if prev.get("version") != cur.get("version"):
        return RadarChanges(False, f"the previous record's radar is version "
                            f"{prev.get('version')!r}, this one is {cur.get('version')!r}; "
                            f"shapes differ, so nothing is compared", prev_stamp, prev_week)
    if prev_week != current.get("week"):
        return RadarChanges(False, f"week rollover — the previous record is week "
                            f"{prev_week}, this page is week {current.get('week')}; "
                            f"verdicts are this week's only and are never compared "
                            f"across weeks", prev_stamp, prev_week)
    for key in ("league_id", "my_roster_id"):
        if str(previous.get(key) or "") != str(current.get(key) or ""):
            return RadarChanges(False, f"the previous record is for a different {key}",
                                prev_stamp, prev_week)

    refreshed: list[str] = []
    if str(prev.get("snapshot_as_of") or "") != str(cur.get("snapshot_as_of") or ""):
        refreshed.append(f"league snapshot {prev.get('snapshot_as_of')} → "
                         f"{cur.get('snapshot_as_of')}")
    pev, cev = prev.get("evidence") or {}, cur.get("evidence") or {}
    for name in sorted(set(pev) | set(cev)):
        a, b = pev.get(name), cev.get(name)
        if a != b and b is not None:
            refreshed.append(f"{name} {_stamp(a) or 'never'} → {_stamp(b)}")

    items: list[RadarChange] = []
    was_pool, now_pool = _index(prev.get("pool")), _index(cur.get("pool"))
    now_owned = {normalize_id(i) for i in (cur.get("owned") or [])}
    new_ids = sorted(now_pool.keys() - was_pool.keys())
    if new_ids:
        rows = [now_pool[i] for i in new_ids]
        items.append(RadarChange("available", f"{len(rows)} newly available",
                                 "on no roster now, on one (or not in the pool) in the "
                                 "previous record: " + _names(rows)))
    gone_ids = sorted(was_pool.keys() - now_pool.keys())
    owned = [was_pool[i] for i in gone_ids if i in now_owned]
    left = [was_pool[i] for i in gone_ids if i not in now_owned]
    if owned:
        items.append(RadarChange("owned", f"{len(owned)} now owned",
                                 "claimed by a roster since the previous record, so no "
                                 "longer suggested: " + _names(owned)))
    if left:
        items.append(RadarChange("gone", f"{len(left)} left the pool",
                                 "no longer eligible (inactive, no team, or position not "
                                 "projectable) and on no roster: " + _names(left)))

    was_c, now_c = _index(prev.get("candidates")), _index(cur.get("candidates"))
    for sid in sorted(now_c.keys() & was_c.keys()):
        a, b = was_c[sid], now_c[sid]
        name = str(b.get("name") or sid)
        if str(a.get("verdict")) != str(b.get("verdict")):
            items.append(RadarChange("verdict", name,
                                     f"{a.get('verdict')} → {b.get('verdict')}: "
                                     f"{b.get('reason')}", sid))
        pa, pb = a.get("projected"), b.get("projected")
        if isinstance(pa, (int, float)) and isinstance(pb, (int, float)):
            if abs(float(pb) - float(pa)) >= MOVE_POINTS:
                items.append(RadarChange("projection", name,
                                         f"{pa:.2f} → {pb:.2f} ({pb - pa:+.2f})", sid))
        elif (pa is None) != (pb is None):
            items.append(RadarChange("projection", name,
                                     "now projectable" if pb is not None else
                                     "no longer projectable", sid))
    # A LINEUP candidate that vanished from the list without leaving the
    # pool: the move is off, and the reader should not have to notice that
    # a card is simply missing.
    for sid in sorted(was_c.keys() - now_c.keys()):
        a = was_c[sid]
        if a.get("verdict") == LINEUP and sid in now_pool:
            items.append(RadarChange("verdict", str(a.get("name") or sid),
                                     f"{LINEUP} → not listed (no longer projected or "
                                     f"movable this week)", sid))

    pst, cst = prev.get("status") or {}, cur.get("status") or {}
    for name in sorted(set(pst) | set(cst)):
        a, b = pst.get(name), cst.get(name)
        if a != b and a is not None and b is not None:
            items.append(RadarChange("evidence", name, f"{str(a).upper()} → {str(b).upper()}"
                                     + (" — evidence expired; verdicts resting on it are "
                                        "withheld" if b != "fresh" else "")))
    return RadarChanges(True, "", prev_stamp, prev_week, tuple(refreshed), tuple(items))
