"""The IR-slot lock (gridiron.irslot, the board alert, the move pre-flight and
the weekly review). Invented players only.

Pins the 2026 week-10 lock: a back activated on Saturday (tag cleared) was
still in the IR slot at the Sunday 9:30 ET kickoff, and Sleeper blocked every
lineup move for the day. The guard must:
  1. flag an IR-slot player whose tag the league does not allow there — no
     tag, Questionable — and pass one tagged Out/IR/Doubtful/... (the
     league's verified list);
  2. treat an unreadable tag as unknown and flag it, never assume eligible;
  3. give the deadline as the first kickoff still to come among the team's
     players, and say when his own game has already locked him;
  4. reach the first screen of the board, the review header and the
     pre-flight.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from gridiron import league_config as LC
from gridiron import movecheck as M
from gridiron.dashboard import _ir_alert
from gridiron.irslot import eligible, ir_traps

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)           # Sunday 07:00 ET
EARLY = NOW + timedelta(hours=2, minutes=30)                # the 9:30 ET game
LATE = NOW + timedelta(hours=6)


def test_the_leagues_ir_list_is_what_sleeper_allows_and_questionable_is_not_on_it():
    assert {"IR", "OUT", "DOUBTFUL"} <= LC.IR_ELIGIBLE_DESIGNATIONS
    assert not eligible("Questionable") and not eligible("") and not eligible(None)
    assert eligible("Out") and eligible(" ir ")
    assert set(LC.IR_ALLOW_FLAGS) <= LC.IR_ELIGIBLE_DESIGNATIONS


def test_an_activated_player_in_the_slot_is_a_trap_with_the_first_kickoff_as_deadline():
    slot = [("a", "Pat Activated", "", EARLY), ("b", "Ike Injured", "Out", LATE),
            ("c", "Quinn Q", "Questionable", LATE), ("d", "Una Unknown", None, LATE)]
    traps = ir_traps(slot, now=NOW, roster_kickoffs=[EARLY, LATE, None])
    assert [t.sleeper_id for t in traps] == ["a", "c", "d"]
    a = traps[0]
    assert a.deadline == EARLY and not a.locked
    assert "no injury tag" in a.describe() and "before Sun 14:30 UTC" in a.describe()
    assert "could not read" in traps[2].describe()
    late = ir_traps(slot[:1], now=EARLY + timedelta(minutes=5), roster_kickoffs=[EARLY, LATE])
    assert late[0].locked and "cannot be moved" in late[0].describe()
    assert late[0].deadline == LATE


def test_the_board_review_and_preflight_all_say_so():
    t = ir_traps([("a", "Pat Activated", "", EARLY)], now=NOW, roster_kickoffs=[EARLY])
    page = _ir_alert(SimpleNamespace(ir_traps=tuple(t), ir_tags_as_of="Sun 11:00 UTC"))
    assert "LOCKED" in page and "Pat Activated" in page and "Sun 11:00 UTC" in page
    assert _ir_alert(SimpleNamespace(ir_traps=())) == ""

    spec = importlib.util.spec_from_file_location("weekly_review",
                                                  ROOT / "scripts/weekly/weekly_review.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weekly_review"] = mod
    spec.loader.exec_module(mod)
    head = "\n".join(mod.header(mod.Review({"week": 10, "ir_traps": [t[0].record()],
                                             "ir_tags_as_of": "Sun 11:00 UTC"})))
    assert "LINEUP LOCKED" in head and "Pat Activated" in head

    rec = {"roster": [{"sleeper_id": "a", "name": "Pat Activated", "position": "RB",
                       "team": "AAA", "lineup": "IR"},
                      {"sleeper_id": "b", "name": "Ike Injured", "position": "RB",
                       "team": "AAA", "lineup": "IR"}]}
    ix = M.Index.from_record(rec)
    live = M.live_rows([{"player_id": "a", "stats": {}, "player": {"position": "RB",
                                                                    "team": "AAA"}},
                        {"player_id": "b", "stats": {}, "player": {"position": "RB",
                                                                    "team": "AAA",
                                                                    "injury_status": "Out"}}])
    a = M.card("a", ix, live=live, byes={}, qbs={})
    b = M.card("b", ix, live=live, byes={}, qbs={})
    assert any("IR-ELIGIBLE" in f for f in a.flags)
    assert not any("IR SLOT" in f for f in b.flags)
