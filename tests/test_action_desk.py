"""The Action Desk: the Next Decision section deepened into one prioritised
set of cards (PR #7, phase 3).

Pinned here:
  * at most three SUPPORTED cards (an endorsed lineup move, or a pickup that
    is conditional only on availability), ranked, the best one first;
  * two pickups that cost the same drop are ONE "pick one" card;
  * a move withheld only because designations are stale, or a pickup whose
    drop is unverified, is a CHECK IN SLEEPER item naming the exact check;
  * a within-noise swap is optional and never takes a top slot;
  * HOLD when nothing is justified, and a headline that says which;
  * every card states why now, benefit, cost, the alternative, the exact
    unresolved check and how long it is valid (UNKNOWN, never invented);
  * the desk is the first section on the page and links into the radar.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from gridiron.dashboard import Action, action_desk
from gridiron.freshness import SourceFreshness, Status
from gridiron.gating import build_gate

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
NAMES = ("sleeper_league", "sleeper_players", "injuries", "schedules")


def _gate(stale=()):
    return build_gate([SourceFreshness(n, Status.STALE if n in stale else Status.FRESH,
                                       KICK, 1, None, "pulled 30h ago" if n in stale else "ok")
                       for n in NAMES])


def swap(delta=3.0, z=1.2, status="ACTIONABLE", urgency="THIS WEEK", deadline=KICK):
    return Action("swap", status, "INFO" if z < 0.5 else urgency,
                  f"WR: start B over S", "edge", deadline, "act before kickoff", "backup",
                  neutral_headline="WR: the last snapshot projected B above S",
                  neutral_detail="was", delta_points=delta, z=z, player_ids=("b", "s"),
                  slot="WR", why_now="first kickoff among the two locks it",
                  benefit=f"+{delta:.2f} projected pts this week", cost="S goes to the bench",
                  names=("B", "S"))


def pickup(add, drop, gain, *, status="CONDITIONAL", order=0, drop_check=""):
    return Action("acquire", status, "INFO", f"If available, claim {add}", "Benefit: ...",
                  None, "waiver timing NOT established", f"next verified drop is X",
                  neutral_headline=f"The last snapshot found {add} would have improved",
                  neutral_detail="was", verify=("Open the player in Sleeper: it shows FREE AGENT",)
                  + ((drop_check,) if drop_check else ()),
                  delta_points=gain, order=order, lapses_at=KICK,
                  player_ids=(add.lower(), drop.lower()), slot="FLEX",
                  benefit=f"+{gain:.2f} to this week's best legal lineup",
                  cost=f"drop {drop}", names=(add, drop), drop_check=drop_check)


def test_at_most_three_supported_cards_best_first_and_the_rest_under_more():
    acts = [swap(4.0), swap(3.0), pickup("A1", "D1", 6.0), pickup("A2", "D2", 5.0, order=1),
            pickup("A3", "D3", 1.0, order=2)]
    desk = action_desk(acts, _gate(), valid_until=None, designations_as_of="Sat 11:00 UTC")
    assert len(desk.top) == 3 and len(desk.more) == 2
    assert all(i.label in ("LINEUP MOVE", "IF AVAILABLE") for i in desk.top)
    assert "move" in desk.headline and "pickup" in desk.headline


def test_two_pickups_that_share_a_drop_are_one_pick_one_card():
    acts = [pickup("A1", "D1", 6.0), pickup("A2", "D1", 5.0, order=1)]
    desk = action_desk(acts, _gate(), valid_until=None, designations_as_of="x")
    assert len(desk.top) == 1
    card = desk.top[0]
    assert card.title.startswith("Pick one") and "D1" in card.title
    assert len(card.actions) == 2


def test_a_within_noise_swap_is_optional_and_never_on_top():
    desk = action_desk([swap(0.5, z=0.2)], _gate(), valid_until=None, designations_as_of="x")
    assert desk.top == () and [i.label for i in desk.more] == ["OPTIONAL"]
    assert desk.headline.startswith("Hold")


def test_every_supported_card_answers_the_six_questions():
    desk = action_desk([swap(), pickup("A1", "D1", 6.0)], _gate(),
                       valid_until=datetime(2026, 9, 27, 4, 0, tzinfo=UTC),
                       designations_as_of="Sat 26 Sep 11:00 UTC")
    for item in desk.top:
        rows = dict(item.rows)
        for k in ("Why now", "Benefit", "Cost", "If not", "Check", "Valid until"):
            assert rows.get(k), (item.title, k)
        # valid until is the EARLIER of the move's kickoff and the evidence expiry
        assert "04:00 UTC" in rows["Valid until"]
    pick = next(i for i in desk.top if i.label == "IF AVAILABLE")
    assert "FREE AGENT" in dict(pick.rows)["Check"]


def test_an_unknown_deadline_is_said_to_be_unknown():
    desk = action_desk([swap(deadline=None)], _gate(), valid_until=None, designations_as_of="x")
    assert "UNKNOWN" in dict(desk.top[0].rows)["Valid until"]


def test_stale_designations_alone_turn_moves_into_check_in_sleeper_items():
    g = _gate(stale=("sleeper_players",))
    acts = [swap(status="WITHHELD"), pickup("A1", "D1", 6.0, status="WITHHELD")]
    desk = action_desk(acts, g, valid_until=None, designations_as_of="Sat 26 Sep 11:00 UTC")
    assert desk.top == ()
    assert [i.label for i in desk.checks] == ["CHECK IN SLEEPER"] * 2
    for i in desk.checks:
        check = dict(i.rows)["Check"]
        assert "status tag" in check and "Sat 26 Sep 11:00 UTC" in check
        assert i.title.startswith(("WR: the last snapshot", "The last snapshot"))  # never imperative
    assert "B" in dict(desk.checks[0].rows)["Check"] and "S" in dict(desk.checks[0].rows)["Check"]
    assert desk.headline == "Check Sleeper before acting"


def test_a_pickup_with_an_unverified_drop_is_a_check_not_a_supported_move():
    acts = [pickup("A1", "D1", 6.0, drop_check="D1's game has started; open D1 in Sleeper and see whether it offers a drop")]
    desk = action_desk(acts, _gate(), valid_until=None, designations_as_of="x")
    assert desk.top == () and desk.checks[0].label == "CHECK IN SLEEPER"
    assert "whether it offers a drop" in dict(desk.checks[0].rows)["Check"]


def test_other_stale_inputs_are_withheld_and_the_desk_holds():
    g = _gate(stale=("sleeper_league",))
    desk = action_desk([swap(status="WITHHELD")], g, valid_until=None, designations_as_of="x")
    assert desk.top == () and desk.checks == ()
    assert [i.label for i in desk.withheld] == ["WITHHELD"]
    assert desk.headline == "Hold — inputs are stale"


def test_nothing_to_do_is_a_hold():
    desk = action_desk([], _gate(), valid_until=None, designations_as_of="x")
    assert desk.headline == "Hold — no change needed" and desk.top == ()


# ------------------------------------------------------------ on the page

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("desk_scenarios", "scripts/weekly/dashboard_scenarios.py")
CLI = _load("desk_dashboard_cli", "scripts/weekly/dashboard.py")


def render(tmp_path: Path, kind: str) -> tuple[str, dict]:
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=SCN.NOW)
    out = tmp_path / "out" / kind
    CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch" / kind),
              "--now", SCN.NOW.isoformat()])
    return ((out / "dashboard_latest.html").read_text("utf-8"),
            json.loads((out / "dashboard_latest.json").read_text("utf-8")))


def test_the_desk_is_the_first_section_and_links_into_the_radar(tmp_path):
    html, rec = render(tmp_path, "complete")
    body = html.split("<main>", 1)[1]
    first_h2 = re.search(r"<h2[^>]*>(.*?)</h2>", body).group(1)
    assert first_h2.startswith("Action Desk — week 3")
    desk = body.split('id="desk"', 1)[1].split("</section>", 1)[0]
    top = desk.split('class="more"', 1)[0]
    assert 1 <= top.count('class="dcard') <= 3
    for sid in {a["player_ids"][0] for a in rec["actions"] if a["kind"] == "acquire"}:
        assert f'href="#fa-{sid}"' in desk and f'id="fa-{sid}"' in html
    # the pick-one grouping: the fixture's two pickups share their drop
    assert "Pick one" in top
    # every top card carries a live deadline the reader's clock can lapse
    assert re.search(r'class="dcard[^"]*"[^>]*data-deadline=', top)


def test_the_hold_scenario_says_hold_first(tmp_path):
    html, _ = render(tmp_path, "hold")
    head = html.split('id="desk"', 1)[0]
    assert "Hold — no change needed" in head or "Hold — no change needed" in html.split("</h1>")[0]
    assert "Hold — no supported change" in html


def test_the_stale_scenario_never_headlines_a_move(tmp_path):
    html, _ = render(tmp_path, "stale")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1)
    assert "Hold" in h1 or "Check Sleeper" in h1
    assert "DEGRADED" in html                       # the warning stays visible


def test_only_stale_designations_render_check_in_sleeper_cards(tmp_path):
    html, rec = render(tmp_path, "designations")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1)
    assert h1 == "Check Sleeper before acting"
    desk = html.split('id="desk"', 1)[1].split("</section>", 1)[0]
    assert "Check in Sleeper first" in desk and "CHECK IN SLEEPER" in desk
    assert "status tag beside each name" in desk
    assert "If available, claim" not in desk.split("Full reasoning")[0]   # face never imperative
    assert "Hold — no supported change" not in desk      # the checks are the answer
