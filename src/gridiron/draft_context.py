"""Historical team context only. No fitted signal or projection adjustment."""
from datetime import datetime
from statistics import mean

ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}
METRICS = ("pass_rush", "run_stop", "pass_block", "run_block")
PERIODS = {"early": (1, 4), "regular": (1, 14), "playoffs": (15, 17)}


def team_code(value):
    code = str(value or "").strip().upper()
    return ALIASES.get(code, code)


def build_context(snapshot, games, *, season, as_of):
    """Use prior-season metrics and schedule identities only, never game outcomes.

    Opponent averages are rounded historical win rates, not opponent-adjusted
    defense ratings. No shrinkage weight or player-role mixture is invented.
    """
    cutoff = datetime.fromisoformat(as_of)
    published = datetime.fromisoformat(snapshot["published"])
    if cutoff.tzinfo is None or published.tzinfo is None:
        raise ValueError("Context dates must include timezone")
    if published > cutoff or int(snapshot["season"]) >= season:
        raise ValueError("Future information cannot enter draft context")
    teams = {}
    for row in snapshot["teams"]:
        code = team_code(row["team"])
        if not code or code in teams:
            raise ValueError("Duplicate or missing team code")
        for metric in METRICS:
            value = row.get(metric)
            if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 100):
                raise ValueError("Invalid win rate")
        for metric in ("pass_rank", "run_rank"):
            value = row.get(metric)
            if value is not None and (not isinstance(value, int) or not 1 <= value <= 32):
                raise ValueError("Invalid blocking rank")
        teams[code] = row
    schedule, seen = {}, set()
    for game in games:
        if int(game["season"]) != season or game["game_type"] != "REG":
            continue
        week = int(game["week"])
        if not 1 <= week <= 18:
            raise ValueError("Invalid regular-season week")
        away, home = team_code(game["away_team"]), team_code(game["home_team"])
        if not away or not home or away == home:
            raise ValueError("Invalid scheduled teams")
        for team, opp, venue in ((away, home, "away"), (home, away, "home")):
            key = (team, week)
            if key in seen:
                raise ValueError("Duplicate team/week in schedule")
            seen.add(key)
            schedule.setdefault(team, []).append({"week": week, "opponent": opp, "venue": venue})
    result = {}
    for code in sorted(set(teams) | set(schedule)):
        row = teams.get(code, {})
        item = {"adjustment": 0, "confidence": "low", "periods": {}}
        for field, rank in (("run_block", "run_rank"), ("pass_block", "pass_rank")):
            item[field] = {"rate": row.get(field), "rank": row.get(rank)}
        for period, (start, end) in PERIODS.items():
            opponents = sorted((g for g in schedule.get(code, []) if start <= g["week"] <= end),
                               key=lambda g: g["week"])
            summary = {"opponents": opponents, "games": len(opponents)}
            for field in ("run_stop", "pass_rush"):
                values = [teams.get(g["opponent"], {}).get(field) for g in opponents]
                summary[field + "_coverage"] = sum(v is not None for v in values)
                summary[field] = (round(mean(values), 1) if values and all(v is not None for v in values)
                                  else None)
            item["periods"][period] = summary
        result[code] = item
    return result
