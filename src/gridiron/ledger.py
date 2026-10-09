"""Draft-night decision ledger (rule #7): every pick with the board's value
at the time, and for my picks the rejected alternative.

Input shapes
- picks: Sleeper `draft/{id}/picks` rows (pick_no, round, picked_by,
  player_id, metadata{first_name,last_name,position,team}).
- board: the draft board frame (sleeper_id, name, pos, team, proj, vor, adp,
  adp_sd, optionally p{pick} availability columns).
"""
from __future__ import annotations

import pandas as pd

BOARD_COLS = ["proj", "vor", "adp", "adp_sd"]


def record_draft(picks: list[dict], board: pd.DataFrame, my_user_id: str,
                 my_draft_slot: int | None = None) -> pd.DataFrame:
    """One row per pick, joined to the board by Sleeper player id."""
    b = board.set_index(board.sleeper_id.astype(str))
    remaining = b.sort_values("vor", ascending=False)
    rows = []
    for p in sorted(picks, key=lambda x: x["pick_no"]):
        md = p.get("metadata") or {}
        pid = str(p.get("player_id"))
        mine = (p.get("draft_slot") == my_draft_slot if my_draft_slot is not None
                and p.get("draft_slot") is not None else str(p.get("picked_by")) == str(my_user_id))
        row = dict(pick=int(p["pick_no"]), round=int(p["round"]), manager=str(p.get("picked_by")),
                   player=f"{md.get('first_name', '')} {md.get('last_name', '')}".strip(),
                   pos=md.get("position"), team=md.get("team"), sleeper_id=pid, mine=mine,
                   alt_player=None, alt_vor=None, vor_given_up=None, reach=None,
                   alt_source="retrospective_best_vor_benchmark" if mine else None)
        for c in BOARD_COLS:
            row[c] = float(b.at[pid, c]) if pid in b.index else None
        if pid in b.index:
            row["reach"] = round(row["adp"] - row["pick"], 2)  # + = taken before the market would
        if mine:
            alt = remaining[remaining.index != pid].head(1)
            if len(alt):
                row["alt_player"] = alt.name.iloc[0]
                row["alt_vor"] = float(alt.vor.iloc[0])
                if pid in b.index:
                    row["vor_given_up"] = row["alt_vor"] - row["vor"]
        remaining = remaining[remaining.index != pid]
        rows.append(row)
    return pd.DataFrame(rows)


def grade_availability(ledger: pd.DataFrame, board: pd.DataFrame, my_picks: list[int],
                       prefix: str = "p") -> dict:
    """Brier score of the board's P(available at my pick k) columns (`p{k}`)
    against who was really still on the board when pick k came up.

    Picks that never happened (ledger ends early) are skipped. Returns
    {"brier", "n", "by_pick": {k: {"n", "expected", "available"}}} where
    expected/available are the predicted and actual counts of survivors.
    """
    last_pick = int(ledger.pick.max()) if len(ledger) else 0
    taken_before = {k: set(ledger.loc[ledger.pick < k, "sleeper_id"].astype(str))
                    for k in my_picks if k <= last_pick}
    sq_err, n, by_pick = 0.0, 0, {}
    ids = board.sleeper_id.astype(str)
    for k, gone in taken_before.items():
        col = f"{prefix}{k}"
        if col not in board.columns:
            continue
        probabilities = pd.to_numeric(board[col], errors="coerce")
        valid = probabilities.notna()
        if not probabilities[valid].between(0, 1).all():
            raise ValueError("availability probabilities must be in [0, 1]")
        p = probabilities[valid].to_numpy(dtype=float)
        y = (~ids[valid].isin(gone)).to_numpy(dtype=float)
        sq_err += float(((p - y) ** 2).sum()); n += len(p)
        by_pick[k] = {"n": len(p), "expected": float(p.sum()), "available": int(y.sum())}
    return {"brier": sq_err / n if n else float("nan"), "n": n, "by_pick": by_pick}


def compare_availability(ledger: pd.DataFrame, board: pd.DataFrame,
                         my_picks: list[int]) -> dict:
    """Grade both PRE-DRAFT models on identical player/pick support.

    Room outcomes include picks by every manager, including the user, matching
    the neutral-market simulation. This does not grade live conditional odds.
    """
    common = board.copy()
    for k in my_picks:
        a, h = f"p{k}", f"ph{k}"
        if a not in common or h not in common:
            common = common.drop(columns=[c for c in (a, h) if c in common])
            continue
        keep = common[a].notna() & common[h].notna()
        common.loc[~keep, [a, h]] = float("nan")
    return {name: grade_availability(ledger, common, my_picks, prefix)
            for name, prefix in [("adp", "p"), ("room_snapshot", "ph")]}
