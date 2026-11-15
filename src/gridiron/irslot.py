"""The IR-slot lock: a player in the IR slot who is no longer IR-eligible.

Sleeper lets a player sit in the IR slot only while he carries a tag the
league allows there (`league_config.IR_ELIGIBLE_DESIGNATIONS`, verified from
the league's `reserve_allow_*` settings). When the tag clears — the player is
activated, or upgraded to Questionable — and he is still in the slot, Sleeper
blocks EVERY lineup move, add and drop for that team until he is moved out.
And he cannot be moved once his own game kicks off.

2026 week 10: the owner's back went from Out to no tag on the Saturday, was
still in the IR slot when his game kicked off at 9:30 ET on Sunday, and froze
the whole lineup for the day — an inactive receiver could not be swapped out,
and a bench upgrade could not go in. Nothing warned, because every check
looked at the slot's player as "on IR", not at whether the slot still held.

So this module answers one question for the board, the move pre-flight and
the weekly review: who is in the IR slot without an eligible tag, and by when
must he be moved (the first kickoff among the team's players: after it, the
lineup is locked for whoever has not played yet as well). Rule #11: the tag is
read raw; "in the IR slot" is never taken to mean "IR-eligible".
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from gridiron.league_config import IR_ELIGIBLE_DESIGNATIONS


@dataclass(frozen=True)
class IrTrap:
    sleeper_id: str
    name: str
    tag: str                    # the current tag, "" when he has none
    deadline: datetime | None   # first kickoff among the team's players still to come
    locked: bool                # his own game has kicked off: he cannot be moved now

    def describe(self) -> str:
        tag = ("a tag this build could not read (player map missing him)" if self.tag == "?"
               else f"tagged {self.tag}" if self.tag else "no injury tag")
        when = ("his game has kicked off, so he cannot be moved until it ends; Sleeper "
                "blocks every lineup move until then" if self.locked else
                "move him to the bench before "
                + (self.deadline.strftime("%a %H:%M UTC") if self.deadline else
                   "the first kickoff of your players")
                + ", or Sleeper blocks every lineup move, add and drop")
        return (f"{self.name} is in your IR slot with {tag}, which the league does not "
                f"allow there: {when}")

    def record(self) -> dict:
        return {"id": self.sleeper_id, "name": self.name, "tag": self.tag,
                "deadline": None if self.deadline is None else self.deadline.isoformat(),
                "locked": self.locked, "text": self.describe()}


def eligible(tag: object) -> bool:
    return str(tag or "").strip().upper() in IR_ELIGIBLE_DESIGNATIONS


def ir_traps(slot: Iterable[tuple[str, str, object, datetime | None]], *, now: datetime,
             roster_kickoffs: Iterable[datetime | None] = ()) -> list[IrTrap]:
    """`slot` is (sleeper_id, name, current tag, his kickoff) for each player in
    the IR slot; `roster_kickoffs` the kickoffs of every rostered player this
    week. A tag of None means UNKNOWN and is reported, never assumed eligible."""
    future = sorted(k for k in roster_kickoffs if k is not None and k > now)
    deadline = future[0] if future else None
    out = []
    for sid, name, tag, kick in slot:
        if tag is not None and eligible(tag):
            continue
        out.append(IrTrap(str(sid), str(name), "?" if tag is None else str(tag).strip(),
                          deadline, kick is not None and kick <= now))
    return out
