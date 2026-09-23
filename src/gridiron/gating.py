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

Box-score sources (`weekly_stats`, `snap_counts`) are deliberately not gated
on AGE. They are old by construction for most of every week — that is what
the evidence boundary exists to state — and withholding every action every
Wednesday because Sunday's box scores are four days old trains the reader to
ignore the gate.

Age is the wrong question for them; COVERAGE is the right one, and
`box_score_blockers` asks it. Box scores feed every projection on the page,
so what matters is whether they have caught up to the evidence boundary the
page is reasoning from. Four days behind on a Wednesday is the publication
cadence (rule #8). Six WEEKS behind is a cache nobody has refreshed since
September, and every projection built on it is a statement about a different
season — which the old exemption, being unconditional, would have passed
through in silence for as long as the file sat there.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from gridiron.freshness import SourceFreshness, Status, expires_at

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
    "weekly_stats": "re-run `scripts/ingest/pull_week.py` — the box scores the "
                    "projections are built from have not caught up to the week "
                    "this page is reasoning about",
    "snap_counts": "re-run `scripts/ingest/pull_week.py` — the snap counts the "
                   "usage priors are built from have not caught up to the week "
                   "this page is reasoning about",
}


#: Sources that feed every projection but are expected to lag by design.
BOX_SCORE_SOURCES: tuple[str, ...] = ("weekly_stats", "snap_counts")

#: How many weeks behind the evidence boundary a box-score source may sit
#: before the actions resting on it are withheld. One week of slack is the
#: publication cadence, not a fudge factor: PFR snap counts trail the box
#: scores, both trail the last game of a week, and on the Tuesday after the
#: boundary advances neither has posted the new week yet. Two weeks behind is
#: a refresh that did not happen.
BOX_SCORE_LAG_WEEKS = 1


def box_score_blockers(sources: Iterable[SourceFreshness], *,
                       evidence_boundary: int | None
                       ) -> dict[str, list[tuple[str, str]]]:
    """Blockers for box-score sources, judged on coverage rather than age.

    Returns `extra`-shaped entries for every action, because every action on
    this page is scored through a projection and every projection is built
    from these frames. A source earns a blocker when it is MISSING, when its
    latest refresh FAILED, when it has a hole inside the weeks it claims to
    cover, or when it has not reached the evidence boundary. Being merely old
    earns nothing.
    """
    reasons: list[tuple[str, str]] = []
    for s in sources:
        if s.name not in BOX_SCORE_SOURCES:
            continue
        if s.status is Status.MISSING:
            reasons.append((s.name, f"no box-score frame at all ({s.reason})"))
            continue
        if s.refresh_failed:
            reasons.append((s.name, f"the latest refresh FAILED, so the newest "
                                    f"thing known about this source is an error "
                                    f"({s.reason})"))
        if s.week_gaps:
            gaps = ",".join(f"wk{w}" for w in s.week_gaps)
            reasons.append((s.name, f"missing {gaps} from inside the weeks it "
                                    f"covers, so every season-to-date number "
                                    f"built on it is short by those weeks"))
        if evidence_boundary is None or evidence_boundary < 1:
            continue
        through = s.covers_through_week
        if through is None:
            reasons.append((s.name, "carries no week coverage, so whether it has "
                                    "caught up to the evidence boundary cannot "
                                    "be established"))
        elif evidence_boundary - int(through) > BOX_SCORE_LAG_WEEKS:
            behind = evidence_boundary - int(through)
            reasons.append((s.name, f"covers only through week {through} while "
                                    f"the page reasons through week "
                                    f"{evidence_boundary} — {behind} weeks "
                                    f"behind, which is a refresh that did not "
                                    f"happen rather than the usual lag"))
    if not reasons:
        return {}
    return {action: list(reasons) for action in ACTIONS}


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


def valid_until(sources: Iterable[SourceFreshness], actions: Sequence[str],
                now: datetime) -> tuple[datetime | None, str]:
    """The first instant at which one of the sources gating `actions` stops
    being FRESH on age, and that source's name; (None, "") when none of
    them will (all already stale or missing, or none gated on age).

    A gate is judged when the page is BUILT. The page is read later, and a
    tab left open overnight would otherwise go on presenting an action as
    supported long after its inputs aged out. The page carries this instant
    and withdraws its moves on the reader's clock once it passes.
    """
    names = {n for a in actions for n in GATED_SOURCES.get(a, ())}
    first: tuple[datetime | None, str] = (None, "")
    for s in sources:
        if s.name not in names:
            continue
        t = expires_at(s, now)
        if t is not None and (first[0] is None or t < first[0]):
            first = (t, s.name)
    return first
