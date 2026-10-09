"""Roster/depth comparisons and optional PFF ingestion. Descriptive, no weights."""
from datetime import datetime
import math
from urllib.parse import urlparse
from gridiron.draft_context import team_code

OL = {"LT", "LG", "C", "RG", "RT", "T", "G", "OT", "OG", "OL"}
SKILL = {"QB", "RB", "FB", "WR", "TE"}

def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must contain timezone")
    return result

def roster_index(rows):
    result = {}
    for row in rows:
        pid = row.get("gsis_id")
        if not pid:
            continue
        if pid in result:
            raise ValueError("Duplicate roster player ID")
        result[pid] = row
    return result

def validate_notes(source, as_of):
    cutoff = stamp(as_of)
    result = {}
    for team, notes in source.get("teams", {}).items():
        for note in notes:
            if stamp(note["published"]) > cutoff:
                raise ValueError("Future coaching or scheme information")
            if note["kind"] not in {"coaching", "play_caller", "scheme"}:
                raise ValueError("Unknown note kind")
            if urlparse(note["source_url"]).scheme != "https":
                raise ValueError("Source must be HTTPS")
        result[team_code(team)] = notes
    return result

def pff_index(rows, rosters, *, season, as_of):
    """Validate supplied component grades, using a unique GSIS/PFF crosswalk.

    One historical season/position per player is supported in this first
    import. No grade averaging or NFL translation of college grades.
    """
    cutoff, known, by_pff = stamp(as_of), {}, {}
    for row in rosters:
        if row.get("gsis_id"):
            known[row["gsis_id"]] = row
            if row.get("pff_id"):
                by_pff.setdefault(str(row["pff_id"]).removesuffix(".0"), set()).add(row["gsis_id"])
    out = {}
    for row in rows:
        pid = row.get("gsis_id", "").strip()
        pff_id = row.get("pff_id", "").strip().removesuffix(".0")
        if pff_id:
            matches = by_pff.get(pff_id, set())
            if len(matches) != 1 or (pid and pid not in matches):
                raise ValueError("Ambiguous or conflicting PFF crosswalk")
            pid = next(iter(matches))
        if pid not in known:
            raise ValueError("Unknown PFF player ID; do not name-match")
        if pid in out:
            raise ValueError("Duplicate PFF player; select one season/position")
        year = int(row["season"])
        if year != season - 1 or stamp(row["published_at"]) > cutoff:
            raise ValueError("PFF import requires prior season and as-of publication")
        if row.get("position") not in OL:
            raise ValueError("PFF blocking import requires an offensive-line position")
        if urlparse(row["source_url"]).scheme != "https":
            raise ValueError("PFF source must be HTTPS")
        record = dict(row, gsis_id=pid)
        for kind in ("run_block", "pass_block"):
            raw = row.get(kind+"_grade", "")
            grade = float(raw) if raw not in ("", None) else None
            snaps = int(row[kind+"_snaps"])
            if snaps < 0 or (grade is not None and (not math.isfinite(grade) or not 0 <= grade <= 100 or snaps == 0)):
                raise ValueError("Invalid PFF grade or snap count")
            record[kind+"_grade"], record[kind+"_snaps"] = grade, snaps
        out[pid] = record
    return out

def build_personnel(previous, current, depths, coaching, *, as_of, grades=None):
    cutoff = stamp(as_of)
    old, new = roster_index(previous), roster_index(current)
    notes = validate_notes(coaching, as_of)
    grades = grades or {}
    teamset = {team_code(x["team"]) for x in current if x.get("team")}
    latest = {}
    for row in depths:
        dt = stamp(row["dt"])
        if dt <= cutoff:
            team = team_code(row["team"])
            latest[team] = max(latest.get(team, dt), dt)
    result = {t: {"arrivals": [], "departures": [], "starters": [], "depth_date": None,
                  "coaching": notes.get(t, []), "adjustment": 0,
                  "identity_warnings": 0} for t in sorted(teamset)}
    def player(row, pid, **more):
        return dict(id=pid, name=row["full_name"], position=row["position"],
                    status=row.get("status", ""), **more)
    for pid, row in new.items():
        t, prior = team_code(row["team"]), old.get(pid)
        if t not in result:
            continue
        if prior is None or team_code(prior["team"]) != t:
            result[t]["arrivals"].append(player(row,pid,previous_team=team_code(prior["team"]) if prior else None,
                evidence="Different team in roster snapshots" if prior else "Not in prior roster snapshot"))
    for pid, row in old.items():
        t, after = team_code(row["team"]), new.get(pid)
        if t in result and (after is None or team_code(after["team"]) != t):
            result[t]["departures"].append(player(row,pid,next_team=team_code(after["team"]) if after else None,
                evidence="Different team in roster snapshots" if after else "Absent from current roster snapshot"))
    groups = {}
    for row in depths:
        t = team_code(row["team"])
        if t not in result or stamp(row["dt"]) != latest.get(t):
            continue
        # The provider's formation label is a chart layout, not measured scheme frequency.
        if row["pos_abb"] not in OL | SKILL:
            continue
        key = (t, row["pos_grp"], row["pos_slot"])
        groups.setdefault(key, []).append(row)
    for (t, _, slot), group in sorted(groups.items()):
        best = min(int(x["pos_rank"]) for x in group)
        top = [x for x in group if int(x["pos_rank"]) == best]
        # Equal-ranked alternatives are kept, never resolved by arbitrary row order.
        for row in top:
            pid = row.get("gsis_id", "")
            matched = new.get(pid)
            conflict = (matched is None or team_code(matched["team"]) != t or
                        (row.get("espn_id") and matched.get("espn_id") and
                         row["espn_id"].removesuffix(".0") != matched["espn_id"].removesuffix(".0")))
            if conflict:
                result[t]["identity_warnings"] += 1
            prior = old.get(pid) if not conflict else None
            change = ("Identity unresolved" if conflict else
                      "No prior-season roster record" if prior is None else
                      "From "+team_code(prior["team"]) if team_code(prior["team"]) != t else
                      "Returning roster member")
            result[t]["starters"].append(dict(id=pid if not conflict else None,name=row["player_name"],
                position=row["pos_abb"],slot=slot,chart_rank=best,alternatives=len(top),
                status=matched.get("status","") if not conflict else "Unresolved",
                change=change,pff=grades.get(pid) if not conflict else None,
                roster_position=matched.get("position") if not conflict else None))
            result[t]["depth_date"] = latest[t].isoformat()
    for item in result.values():
        for key in ("arrivals","departures"):
            item[key].sort(key=lambda x:(x["position"],x["name"]))
    return result
