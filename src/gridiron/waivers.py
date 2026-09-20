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

Two things this module refuses to pretend to know, because getting either
wrong costs a real roster spot:

**A player who scores 0 this week is not worth 0.** A bye, an IR stint, an
Out designation — each produces a withheld 0.0 projection, and ranking the
drop candidates by projected points therefore put the owner's injured RB1 at
the top of the "cheapest to lose" list, ahead of a healthy backup projecting
4. Temporary absence is not zero roster value. Pricing those players properly
needs a rest-of-season model, which does not exist here and is not going to
be invented inline (rule #5: nothing ships without beating the baseline
out-of-sample). So they are PROTECTED: excluded from automatic drop
suggestions and listed separately with the reason, for a human to price.

**Not on a roster is not the same as addable now.** The cached league
snapshot proves one thing — this id appears on no roster's `players` list at
the snapshot's as-of. It does not say whether the player is a free agent or
sitting on waivers, when he was dropped, or whether a claim is already in on
him. Sleeper carries that in the transactions feed, which this repo does not
pull. So eligibility is reported as UNVERIFIED with its evidence and the
league's own waiver configuration, and no upgrade on this board ever says
"add now" (rule #11: no accessor makes the wrong call easy).

Also not modelled, and said so on the page: FAAB pricing (§7 is UNVERIFIED)
and rest-of-season value beyond this week's projection. A pickup whose player
has no projection is listed as unevaluated, never ranked.

The pool is built from ids only. A player is "available" when his Sleeper id
is on no roster in the league snapshot; his projection anchors on the
crosswalk's gsis id (rule #3). Names are display only.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gridiron.ids import Crosswalk, is_dst_id, normalize_id
from gridiron.league_config import (
    WAIVER_BUDGET, WAIVER_CLEAR_DAYS, WAIVER_TYPE,
)
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
class Eligibility:
    """What the cache can and cannot prove about adding this player.

    Never "addable". The snapshot proves absence from every roster at one
    instant; the waiver clock, pending claims and the drop that put him there
    live in Sleeper's transactions feed, which this repo does not pull.
    """

    state: str                        # always "UNVERIFIED" from cache alone
    basis: tuple[str, ...]            # what IS supported, each line sourced
    verify: str                       # the exact check the owner must make

    def describe(self) -> str:
        return f"{self.state} — " + "; ".join(self.basis) + f". {self.verify}"


#: The league's own waiver configuration (rule #1: VERIFIED constants, read
#: from the live league object). It describes the LEAGUE, never this player's
#: individual waiver clock — the page has to keep saying which one it means.
WAIVER_RULE = (
    f"league waivers are {WAIVER_TYPE.upper()} with a {WAIVER_BUDGET}-unit budget "
    f"and a {WAIVER_CLEAR_DAYS}-day clear period (verified league settings). A "
    f"player dropped inside that window is ON WAIVERS, not a free agent — and "
    f"nothing in this cache says when any of these players was dropped."
)


def eligibility(*, snapshot_as_of: str, unrostered_since: str = "") -> Eligibility:
    """Honest add-eligibility for an unrostered player.

    Takes no player id on purpose: there is nothing player-specific to look
    up. Every unrostered player is in exactly the same evidential position —
    absent from the snapshot, with an unknown waiver clock — and a signature
    that accepted an id would imply this function knows something about him
    that it does not.
    """
    basis = [f"on no roster in the league snapshot taken {snapshot_as_of}"]
    if unrostered_since:
        basis.append(f"also unrostered in the previous snapshot ({unrostered_since}), "
                     f"which is evidence of a settled free agent but not proof — he "
                     f"could have been added and dropped again in between")
    basis.append(WAIVER_RULE)
    return Eligibility(
        "UNVERIFIED",
        tuple(basis),
        "Open the player in Sleeper: it shows FREE AGENT or the waiver clear "
        "time. Claim there; this page never submits anything.")


@dataclass(frozen=True)
class WaiverBoard:
    upgrades: tuple[Upgrade, ...]
    pool_size: int
    evaluated: int
    unprojected: int
    droppable: tuple[Player, ...]        # ranked cheapest-to-lose first
    abstained: str = ""
    notes: tuple[str, ...] = field(default=())
    #: Roster players deliberately withheld from the drop ranking, with the
    #: reason. Never empty-by-accident: see `protected_players`.
    protected: tuple[tuple[Player, str], ...] = field(default=())


def protected_players(roster: Sequence[Player]) -> tuple[tuple[Player, str], ...]:
    """Roster players that must NOT be offered as an automatic drop, paired
    with the reason a human has to overrule.

    The categories, and why each is a protection rather than a low ranking:

      * **Withheld zero** — bye, Out, IR, suspended. The 0.0 on the page is a
        statement about THIS WEEK's availability, not about the player. He may
        be the best asset on the roster; sorting by projected points put him
        first in line to be cut. Pricing him needs rest-of-season value, which
        this repo does not model and will not fake (rule #5).
      * **In the IR slot** — same argument, plus dropping him surrenders a
        roster spot the league gave you for free.
      * **Unprojected** — "we could not project him" is not "he is worth
        nothing" (this one was already respected, and stays).
      * **Not provably movable** — a starter who is locked, or whose lock
        state is unknown, cannot be shown to be droppable right now.
    """
    out: list[tuple[Player, str]] = []
    for p in roster:
        if p.lineup == "START" and not p.movable:
            out.append((p, f"starting and not provably movable — {p.lock_reason}"))
        elif p.lineup == "IR":
            # Checked before the withheld branch: an IR player is almost always
            # withheld too, and "you would surrender the roster spot" is the
            # more actionable of the two true statements.
            out.append((p, "in the IR slot — dropping him gives up the roster spot "
                        "the league grants for an injured player, and his 0 this "
                        "week says nothing about the rest of the season"))
        elif not p.projected:
            out.append((p, "no projection: " + "; ".join(p.projection.reasons)
                        + " — unknown value is not zero value"))
        elif p.projection.is_withheld:
            model = p.projection.model_mean
            worth = (f"he projected {model:.2f} before the withholding"
                     if isinstance(model, (int, float)) else "his value is unmeasured")
            out.append((p, "projected 0 because he will not play this week ("
                        + "; ".join(p.projection.reasons) + f"), not because he is "
                        f"worth 0 — {worth}, and no rest-of-season model exists here "
                        f"to price the weeks after this one"))
    return tuple(out)


def droppable_players(roster: Sequence[Player]) -> tuple[Player, ...]:
    """Roster players this board may propose dropping, cheapest to lose first.

    "Cheapest" is only meaningful across players whose projections are
    comparable, so everything `protected_players` names is excluded first.
    What remains is ranked by this week's projected points, which is still a
    one-week view — the page says so, and the number next to each drop is the
    cost being paid.
    """
    held = {id(p) for p, _ in protected_players(roster)}
    cands = [p for p in roster if id(p) not in held]
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
    # `movable`, not `not locked`: a free agent whose team's kickoff cannot be
    # established is not shown entering a lineup this week.
    projected = [p for p in pool if p.projected and p.movable]
    unprojected = pool_size - len([p for p in pool if p.projected])
    protected = protected_players(roster)
    if not locks_known:
        return WaiverBoard((), pool_size, 0, unprojected, (), abstained=(
            "lock state UNKNOWN — no schedule for this week, so whether a "
            "pickup could legally enter the lineup cannot be verified"),
            protected=protected)
    drops = droppable_players(roster)
    if not drops:
        return WaiverBoard((), pool_size, 0, unprojected, (), abstained=(
            f"no droppable player on the roster — all {len(protected)} candidate "
            f"drop(s) are protected (locked, unprojected, or projected 0 only "
            f"because they are not playing this week)"), protected=protected)

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
        "rest-of-season value not modelled — every gain here is THIS WEEK's "
        "projected points only",
        WAIVER_RULE,
        f"{len(protected)} roster player(s) are protected from the drop list "
        f"and named with the reason; a player who scores 0 this week because "
        f"he is not playing is not a player worth 0",
    )
    return WaiverBoard(tuple(upgrades), pool_size, len(candidates), unprojected,
                       drops, notes=notes, protected=protected)


def pool_players(ids: Iterable[str], players: Mapping[str, Mapping[str, object]],
                 crosswalk: Crosswalk, projector, lock) -> list[Player]:
    """Turn available ids into optimizer Players. `projector(sid, gsis, pos,
    team)` returns a Projection; `lock(team)` returns a `gridiron.lineup.Lock`
    whose three-valued state decides whether the pickup can be shown entering
    a lineup at all."""
    out: list[Player] = []
    for sid in ids:
        rec = players.get(sid) or {}
        pos = str(rec.get("position") or "").upper()
        team = str(rec.get("team") or "")
        gid = crosswalk.gsis(sid) or ""
        proj: Projection = projector(sid, gid, pos, team)
        lk = lock(team)
        out.append(Player(sid, str(rec.get("full_name") or f"sleeper:{sid}"), pos,
                          team, proj, "FA", lk.locked, lk.note, "", (), gid,
                          lk.known))
    return out
