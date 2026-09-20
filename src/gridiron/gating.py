"""Which actions the current inputs are good enough to support.

`freshness` answers "how old is this source?". This module answers the only
question the owner actually has on a Sunday morning: **may I act on what
this page says?** Those are different questions, and the dashboard used to
answer only the first one — it printed a DEGRADED banner over a five-day-old
league snapshot and then, underneath it, two lineup changes and three waiver
pickups, stated with exactly the same confidence as on a fresh cache.

A label is not a gate. So every action the page can emit declares the inputs
it depends on, and an action whose inputs are STALE or MISSING is WITHHELD:

  * The comparison that produced it is still shown, in full, clearly marked
    as last-known information with the age of every input behind it. Deleting
    it would destroy the one thing a stale cache is still good for — telling
    you what was true the last time anyone looked.
  * What disappears is the imperative. A withheld action never says "start
    him" or "add him"; it says which input is stale, what to verify, and
    where. The owner decides with the verification in hand.

Rule #8 governs the ages themselves (the week has a shape), rule #11 governs
the posture: no accessor makes the wrong call easy, and "the banner was up"
is not consent.

What gates what, and why each one is load-bearing rather than tidy:

  lineup  — `sleeper_league` (the roster and `starters` you would be editing;
            stale means you may be optimising a lineup that no longer
            exists), `sleeper_players` and `injuries` (the designations that
            decide who is startable at all), `schedules` (kickoff legality).
  waiver  — everything `lineup` needs, because a pickup is scored by the
            lineup change it produces, plus `sleeper_league` again for who is
            rostered: a five-day-old roster list is a five-day-old answer to
            "is this player even available".
  matchup — `sleeper_league` and `sleeper_players`: the opponent's lineup and
            both sides' designations.

Box-score sources (`weekly_stats`, `snap_counts`) are deliberately NOT gates.
They are stale by construction for most of every week — that is what the
evidence boundary exists to state — and gating on them would withhold every
action every Wednesday, which trains the reader to ignore the gate.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gridiron.freshness import SourceFreshness, Status

#: Actions the dashboard can emit, and the sources each one rests on.
GATED_SOURCES: dict[str, tuple[str, ...]] = {
    "lineup": ("sleeper_league", "sleeper_players", "injuries", "schedules"),
    "waiver": ("sleeper_league", "sleeper_players", "injuries", "schedules"),
    "matchup": ("sleeper_league", "sleeper_players"),
}

ACTIONS: tuple[str, ...] = tuple(GATED_SOURCES)

#: What to do about each stale input, in the owner's terms. Printed next to
#: the withheld action so "verify first" is a task, not a mood.
VERIFY: dict[str, str] = {
    "sleeper_league": "open Sleeper and confirm your roster and starting lineup",
    "sleeper_players": "check the player's game status in Sleeper (the live "
                       "designation is the field that moves last)",
    "injuries": "check this week's practice report / game designations",
    "schedules": "confirm the kickoff time for the affected game",
    "crosswalk": "re-run the ingest so ids resolve",
}


@dataclass(frozen=True)
class Gate:
    """One action and the reason it is or is not supported."""

    action: str
    blockers: tuple[tuple[str, Status, str], ...] = field(default=())

    @property
    def allowed(self) -> bool:
        return not self.blockers

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(name for name, _, _ in self.blockers)

    def why(self) -> str:
        if self.allowed:
            return ""
        parts = [f"{name} is {status.value.upper()} ({reason})"
                 for name, status, reason in self.blockers]
        return "; ".join(parts)

    def verify(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name, _, _ in self.blockers:
            step = VERIFY.get(name)
            if step and step not in seen:
                seen.append(step)
        return tuple(seen)

    def banner(self) -> str:
        if self.allowed:
            return ""
        return (f"WITHHELD — {self.why()}. The comparison below is LAST KNOWN "
                f"information, not current advice. Verify first: "
                + "; ".join(self.verify()) + ".")


@dataclass(frozen=True)
class ActionGate:
    """The whole gate: one `Gate` per action, plus the sentence for the page."""

    gates: Mapping[str, Gate]

    def allows(self, action: str) -> bool:
        gate = self.gates.get(action)
        return gate is None or gate.allowed

    def gate(self, action: str) -> Gate:
        return self.gates.get(action) or Gate(action)

    def why(self, action: str) -> str:
        return self.gate(action).why()

    def banner(self, action: str) -> str:
        return self.gate(action).banner()

    @property
    def withheld(self) -> tuple[str, ...]:
        return tuple(a for a in ACTIONS if not self.allows(a))

    def notes(self) -> tuple[str, ...]:
        return tuple(f"{a} actions WITHHELD: {self.why(a)}" for a in self.withheld)

    def record(self) -> dict:
        """The gate as it goes into the decision archive, so a later grader
        can tell an action this page ENDORSED from one it merely displayed."""
        return {a: {"allowed": self.allows(a),
                    "blockers": [{"source": n, "status": s.value, "reason": r}
                                 for n, s, r in self.gate(a).blockers],
                    "verify": list(self.gate(a).verify())} for a in ACTIONS}


def build_gate(sources: Iterable[SourceFreshness],
               *, extra: Mapping[str, Sequence[tuple[str, str]]] | None = None
               ) -> ActionGate:
    """Gate every action on the freshness of the sources it rests on.

    `extra` adds `(source, reason)` blockers that freshness cannot see — the
    one in use is an unreadable schedule, which is fresh on disk and still
    cannot establish a single kickoff. A file's timestamp says when it was
    written, never whether its contents parse.
    """
    by_name = {s.name: s for s in sources}
    gates: dict[str, Gate] = {}
    for action, names in GATED_SOURCES.items():
        blockers: list[tuple[str, Status, str]] = []
        for name in names:
            s = by_name.get(name)
            if s is None:
                blockers.append((name, Status.MISSING,
                                 "freshness was never assessed for this source"))
            elif s.status is not Status.FRESH:
                blockers.append((name, s.status, s.reason))
        for name, reason in (extra or {}).get(action, ()):
            blockers.append((name, Status.STALE, reason))
        gates[action] = Gate(action, tuple(blockers))
    return ActionGate(gates)
