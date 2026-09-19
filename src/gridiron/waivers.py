"""Available-player upgrades with an explicit drop.

The waiver question is never "who is the best free agent". It is "does
adding this player and dropping THAT one make my legal lineup better", and
the drop is half of the decision (rule #7: log the rejected side). So every
upgrade here names three things: the player to add, the player to drop, and
the change in this week's best legal lineup that the pair produces.

Replacement level is forward-looking and pool-shaped (QUANT_FOUNDATIONS
§6.1): the value of a pickup is measured against the lineup he would
actually enter, never against a season-total rank.

Two kinds of pickup are reported, and they are kept apart:

  * LINEUP upgrade — this week's best legal lineup scores more with the
    pair applied. `lineup_gain` > 0.
  * DEPTH upgrade — the lineup is unchanged this week, but the added player
    projects above the dropped one. `depth_gain` > 0, `lineup_gain` = 0.
    Worth doing for the bye weeks ahead; not worth confusing with the first.

Not modelled, and said so on the page: FAAB pricing (§7 is UNVERIFIED), the
waiver-vs-free-agent state of each player, and rest-of-season value beyond
this week's projection. A pickup whose player has no projection is listed
as unevaluated, never ranked.

The pool is built from ids only. A player is "available" when his Sleeper id
is on no roster in the league snapshot; his projection anchors on the
crosswalk's gsis id (rule #3). Names are display only.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gridiron.ids import Crosswalk, is_dst_id, normalize_id
from gridiron.lineup import Player, plan_lineup
from gridiron.projection import PROJECTABLE, Projection

#: How many candidates per position are evaluated for a lineup change.
#: Enough that a real upgrade is never missed; bounded so the page stays
#: readable and the archive stays small.
CANDIDATES_PER_POSITION = 12


def available_ids(players: Mapping[str, Mapping[str, object]],
                  rosters: Iterable[Mapping[str, object]]) -> tuple[str, ...]:
    """Sleeper ids of active, teamed, projectable-position players held by
    NO roster in the league. Team defenses are excluded (no projection)."""
    held: set[str] = set()
    for r in rosters:
        for sid in (r.get("players") or []):
            held.add(normalize_id(sid))
    out = []
    for sid, rec in players.items():
        sid = normalize_id(sid)
        if not sid or sid in held or is_dst_id(sid):
            continue
        rec = rec or {}
        pos = str(rec.get("position") or "").upper()
        if pos not in PROJECTABLE:
            continue
        if not rec.get("team"):
            continue
        if str(rec.get("status") or "Active") != "Active":
            continue
        out.append(sid)
    return tuple(sorted(out))


@dataclass(frozen=True)
class Upgrade:
    add: Player
    drop: Player
    lineup_gain: float          # Δ best legal lineup points this week
    depth_gain: float           # add.value - drop.value
    slot: str                   # the slot the pickup would enter, or ""
    displaces: Player | None    # who leaves the lineup (may be the drop)

    @property
    def kind(self) -> str:
        return "lineup" if self.lineup_gain > 0 else "depth"

    def describe(self) -> str:
        if self.kind == "lineup":
            return (f"add {self.add.name} ({self.add.position}, {self.add.value:.2f}), "
                    f"drop {self.drop.name} ({self.drop.position}, {self.drop.value:.2f}): "
                    f"best lineup {self.lineup_gain:+.2f} pts via {self.slot}")
        return (f"add {self.add.name} ({self.add.position}, {self.add.value:.2f}), "
                f"drop {self.drop.name} ({self.drop.position}, {self.drop.value:.2f}): "
                f"depth {self.depth_gain:+.2f} pts, this week's lineup unchanged")


@dataclass(frozen=True)
class WaiverBoard:
    upgrades: tuple[Upgrade, ...]
    pool_size: int
    evaluated: int
    unprojected: int
    droppable: tuple[Player, ...]        # ranked cheapest-to-lose first
    abstained: str = ""
    notes: tuple[str, ...] = field(default=())


def droppable_players(roster: Sequence[Player]) -> tuple[Player, ...]:
    """Roster players a human COULD drop now, cheapest to lose first.

    A locked starter cannot be dropped mid-game. A player without a
    projection is droppable in the app but is never proposed as the drop
    here: "we do not know what he is worth" is not "he is worth nothing".
    IR-slot players are droppable and rank by their (withheld) projection.
    """
    cands = [p for p in roster if not (p.locked and p.lineup == "START") and p.projected]
    return tuple(sorted(cands, key=lambda p: (p.value or 0.0)))


def _best_points(roster: Sequence[Player], starters: Sequence[str],
                 slots: Sequence[str]) -> tuple[float, tuple[Player | None, ...]]:
    plan = plan_lineup(roster, starters, slots, locks_known=True)
    return plan.best_points, plan.best


def build_board(roster: Sequence[Player], pool: Sequence[Player],
                starters: Sequence[str], slots: Sequence[str], *,
                locks_known: bool) -> WaiverBoard:
    """Evaluate add/drop pairs against this week's best legal lineup."""
    pool_size = len(pool)
    projected = [p for p in pool if p.projected and not p.locked]
    unprojected = pool_size - len([p for p in pool if p.projected])
    if not locks_known:
        return WaiverBoard((), pool_size, 0, unprojected, (), abstained=(
            "lock state UNKNOWN — no schedule for this week, so whether a "
            "pickup could legally enter the lineup cannot be verified"))
    drops = droppable_players(roster)
    if not drops:
        return WaiverBoard((), pool_size, 0, unprojected, (), abstained=(
            "no droppable projected player on the roster — every candidate drop "
            "is either a locked starter or unprojected"))

    base_points, base_best = _best_points(roster, starters, slots)
    base_ids = [b.sleeper_id for b in base_best if b is not None]

    # Top candidates per position by projection; the rest are counted.
    by_pos: dict[str, list[Player]] = {}
    for p in sorted(projected, key=lambda p: (p.value or 0.0), reverse=True):
        by_pos.setdefault(p.position, [])
        if len(by_pos[p.position]) < CANDIDATES_PER_POSITION:
            by_pos[p.position].append(p)
    candidates = [p for ps in by_pos.values() for p in ps]

    upgrades: list[Upgrade] = []
    roster_ids = {p.sleeper_id for p in roster}
    for add in candidates:
        if add.sleeper_id in roster_ids:
            continue
        best_pair: Upgrade | None = None
        for drop in drops:
            trial = [p for p in roster if p.sleeper_id != drop.sleeper_id] + [
                Player(add.sleeper_id, add.name, add.position, add.team,
                       add.projection, "BENCH", add.locked, add.lock_note,
                       add.availability, add.flags, add.gsis_id)]
            trial_starters = [s if str(s) != drop.sleeper_id else "0" for s in starters]
            pts, best = _best_points(trial, trial_starters, slots)
            gain = round(pts - base_points, 3)
            slot, displaced = "", None
            trial_ids = {b.sleeper_id for b in best if b is not None}
            for i, b in enumerate(best):
                if b is not None and b.sleeper_id == add.sleeper_id:
                    slot = slots[i]
                    break
            if slot:
                gone = [sid for sid in base_ids if sid not in trial_ids]
                displaced = next((p for p in roster if p.sleeper_id in gone), None)
            depth = round(float(add.value or 0.0) - float(drop.value or 0.0), 3)
            cand = Upgrade(add, drop, gain, depth, slot, displaced)
            if best_pair is None or (cand.lineup_gain, cand.depth_gain) > (
                    best_pair.lineup_gain, best_pair.depth_gain):
                best_pair = cand
        if best_pair is not None and (best_pair.lineup_gain > 0 or best_pair.depth_gain > 0):
            upgrades.append(best_pair)
    upgrades.sort(key=lambda u: (u.lineup_gain, u.depth_gain), reverse=True)
    notes = (
        "FAAB price not modelled (QUANT_FOUNDATIONS §7 is unverified); "
        "waiver-vs-free-agent status not read; rest-of-season value not "
        "modelled — gains are THIS WEEK's projected points only",
    )
    return WaiverBoard(tuple(upgrades), pool_size, len(candidates), unprojected,
                       drops, notes=notes)


def pool_players(ids: Iterable[str], players: Mapping[str, Mapping[str, object]],
                 crosswalk: Crosswalk, projector, lock) -> list[Player]:
    """Turn available ids into optimizer Players. `projector(sid, gsis, pos,
    team)` returns a Projection; `lock(team)` returns (locked, note)."""
    out: list[Player] = []
    for sid in ids:
        rec = players.get(sid) or {}
        pos = str(rec.get("position") or "").upper()
        team = str(rec.get("team") or "")
        gid = crosswalk.gsis(sid) or ""
        proj: Projection = projector(sid, gid, pos, team)
        locked, note = lock(team)
        out.append(Player(sid, str(rec.get("full_name") or f"sleeper:{sid}"), pos,
                          team, proj, "FA", locked, note, "", (), gid))
    return out
