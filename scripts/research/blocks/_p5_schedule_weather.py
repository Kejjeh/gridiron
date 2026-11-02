"""Package P5 helpers (schedule and weather; not a block: no ID).

Used by QB-11, QB-13, RB-07, RB-11, WR-11, K-05, K-07, K-12, DEF-08 and DEF-10.

Game facts (`team_frame`) are PRE-GAME facts of a team's own game: the
pre-game schedule (closing lines, rest, kickoff, roof, surface, venue), two
committed reference tables, and Open-Meteo's archived forecast for the venue
at kickoff. Nothing there reads a played game, so those columns pass the
runner's leak check by construction. The player-history parts (`deep_share`,
`last_game_touches`) read play-by-play and box scores of weeks before the
row's week only (`_common.as_of`, the team's previous game).

Reference tables (committed, a source on every row):
  data/reference/stadiums.csv        nflverse stadium_id -> lat/lon (Wikidata P625,
                                     revision recorded), elevation (Copernicus DEM
                                     GLO-90 via Open-Meteo), IANA time zone
  data/reference/team_timezones.csv  team (today's code) -> home IANA zone and the
                                     stadium ids of its home games

Venue. The schedule's `stadium_id`, except a neutral-site game listed at the
home team's own stadium (nflverse lists the 2025 international games that
way): that venue is a contradiction, so it is UNKNOWN and every venue-derived
value (roof, surface, elevation, zone, weather) is missing for the game.

Weather. Open-Meteo (https://open-meteo.com/), CC BY 4.0, free API for
non-commercial use: "Weather data by Open-Meteo.com". The archived forecast
(historical-forecast-api, model `gfs_seamless`: HRRR over the US, GFS
elsewhere) at the venue's coordinates is preferred. Open-Meteo assembles it
from the first hours of each model run, so it is a short-range forecast,
close to the observation, not the multi-day forecast a lineup decision sees.
Where it has no values (the international venues before 2021) the game's
weather is UNKNOWN. (Until the 2026-11-02 correction the observed
reanalysis, archive-api ERA5, stood in there: a post-game observation, so a
leak. It is still pulled and counted by `--summary`, never used.) The source
is recorded per game (`wx_source`). Indoors (roof dome or closed) there is no weather: wind 0,
precipitation 0. Window: wind is the mean of the hourly 10 m wind speed (mph)
at the kickoff hour (UTC, floored) and the two hours after it; precipitation
is the sum of the three hourly totals after the kickoff hour (mm, the
game's first three hours). Downloads are cached (gitignored) under
data/research/cache/open_meteo/, one file per venue and season covering
every outdoor game there (weeks 4-18). Pull once (resumable):

    PYTHONPATH=src python scripts/research/blocks/_p5_schedule_weather.py --pull
    PYTHONPATH=src python scripts/research/blocks/_p5_schedule_weather.py --summary

Transforms every block of the package uses (fixed before any score):
  wind10      mph above WIND_FLOOR (10), 0 at or below it and indoors
  precip3h    mm in the first three hours, capped at PRECIP_CAP (10), 0 indoors
  rest_c      days since the team's last game, clipped to [4, 14]
  short_rest  1 when rest <= SHORT_REST (5 days: a Thursday after a Sunday)
  tz_shift    venue UTC offset - the team's home UTC offset at kickoff, hours
              (+ = east, DST-aware), capped at +/- TZ_CAP (3)
  early       kickoff at or before 13:00 Eastern
  total_rel   the closing total - the mean closing total of the previous season

Team codes are today's (OAK -> LV) in every Context frame; `teams` maps again
here, which changes nothing there and keeps the pull path honest.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from functools import cache
from zoneinfo import ZoneInfo

import _common as C
import _p1a_pbp_roles as R
import numpy as np
import pandas as pd

from gridiron.paths import REPO_ROOT, RESEARCH_CACHE

REFERENCE = REPO_ROOT / "data" / "reference"
STADIUMS = REFERENCE / "stadiums.csv"
TEAM_ZONES = REFERENCE / "team_timezones.csv"
OM_CACHE = RESEARCH_CACHE / "open_meteo"
FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_MODEL = "gfs_seamless"
ARCHIVE_MODEL = "era5"
HOURLY = ("wind_speed_10m", "precipitation")
WINDOW_HOURS = 3
INDOORS = ("dome", "closed")
OUTDOORS = ("outdoors", "open")
TURF = ("fieldturf", "matrixturf", "sportturf", "astroturf", "a_turf")
GRASS = ("grass", "dessograss")
EASTERN = ZoneInfo("America/New_York")
TEAM_CODES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
KEY = ["season", "week", "gsis_id"]
#: Wind below about 10 mph does not move passing or kicking; above it, each mph counts.
WIND_FLOOR = 10.0
#: Precipitation over the first three hours, mm; heavier counts as this much.
PRECIP_CAP = 10.0
REST_LO, REST_HI = 4.0, 14.0
#: A short week: a Thursday game after a Sunday game is 4 days of rest.
SHORT_REST = 5.0
#: Time zones crossed count up to this many hours (an international trip
#: would otherwise dominate a linear feature with a handful of games).
TZ_CAP = 3.0
#: An "early" kickoff starts at or before 13:00 Eastern (the 1 p.m. window,
#: Thanksgiving's 12:30 and the 9:30 international games).
EARLY_ET_HOUR = 13.0
#: A deep throw travels at least this far in the air (nflverse `air_yards`).
DEEP_AIR_YARDS = 20.0
#: Pseudo-counts shrinking a season-to-date deep share toward the league rate
#: of the previous season: about a game and a half of throws or targets.
PSEUDO = {"passer": 50.0, "receiver": 10.0}
PASS_COLUMNS = ["play_type", "pass_attempt", "sack", "qb_spike", "two_point_attempt",
                "air_yards", "passer_player_id", "receiver_player_id"]
#: The research rows start at week 4 (block_runner.MIN_WEEK); weather is pulled
#: for those weeks only.
PULL_MIN_WEEK = 4


def teams(series: pd.Series) -> pd.Series:
    """Today's franchise code, whatever the column's dtype."""
    return series.astype(object).replace(TEAM_CODES)


def stadiums() -> pd.DataFrame:
    return pd.read_csv(STADIUMS).set_index("stadium_id")


def team_zones() -> pd.DataFrame:
    z = pd.read_csv(TEAM_ZONES).set_index("team")
    z["home_ids"] = z["home_stadium_ids"].map(lambda v: frozenset(str(v).split(";")))
    return z


def kickoff_utc(gameday: pd.Series, gametime: pd.Series) -> pd.Series:
    """The schedule's gameday + gametime (Eastern clock time) as UTC."""
    out = []
    for d, t in zip(gameday, gametime):
        if not isinstance(d, str) or not isinstance(t, str) or ":" not in t:
            out.append(pd.NaT)
            continue
        local = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
        out.append(pd.Timestamp(local.astimezone(UTC)))
    return pd.Series(pd.to_datetime(out, utc=True), index=gameday.index)


def _offset_hours(kick: pd.Series, zones: pd.Series) -> pd.Series:
    """UTC offset (hours) of each zone at each kickoff instant (DST-aware)."""
    out = []
    for k, z in zip(kick, zones):
        if pd.isna(k) or not isinstance(z, str):
            out.append(np.nan)
            continue
        out.append(k.to_pydatetime().astimezone(ZoneInfo(z)).utcoffset().total_seconds() / 3600)
    return pd.Series(out, index=kick.index, dtype="float64")


def games(ctx, seasons) -> pd.DataFrame:
    """One row per regular-season game of `seasons` from the PRE-GAME schedule:
    kickoff, lines, rest, the venue's facts (missing when the venue is
    unknown) and `total_center`, the previous season's mean closing total."""
    seasons = sorted(int(s) for s in seasons)
    allseasons = sorted(set(seasons) | {s - 1 for s in seasons})
    s = ctx.schedule(allseasons).copy()
    s["home_team"], s["away_team"] = teams(s["home_team"]), teams(s["away_team"])
    center = pd.to_numeric(s["total_line"], errors="coerce").groupby(s["season"]).mean()
    center.index = center.index + 1
    s = s.loc[s["season"].isin(seasons)].copy()
    st, tz = stadiums(), team_zones()
    own = [isinstance(v, str) and h in tz.index and v in tz.loc[h, "home_ids"]
           for v, h in zip(s["stadium_id"], s["home_team"])]
    neutral_at_own = ((s["location"].astype(object) == "Neutral").to_numpy()
                      & np.array(own, dtype=bool))
    venue = s["stadium_id"].astype(object).where(~neutral_at_own)
    venue = venue.where(venue.isin(st.index))
    g = pd.DataFrame({
        "season": s["season"].astype(int), "week": s["week"].astype(int),
        "game_id": s["game_id"].astype(object), "home_team": s["home_team"],
        "away_team": s["away_team"], "venue": venue,
        "kick_utc": kickoff_utc(s["gameday"].astype(object), s["gametime"].astype(object)),
        "weekday": s["weekday"].astype(object),
        "month": pd.to_numeric(s["gameday"].astype(str).str.slice(5, 7), errors="coerce"),
        "spread_line": pd.to_numeric(s["spread_line"], errors="coerce"),
        "total_line": pd.to_numeric(s["total_line"], errors="coerce"),
        "home_rest": pd.to_numeric(s["home_rest"], errors="coerce"),
        "away_rest": pd.to_numeric(s["away_rest"], errors="coerce")})
    g["total_center"] = g["season"].map(center).astype("float64")
    et = g["kick_utc"].map(lambda k: np.nan if pd.isna(k) else
                           k.to_pydatetime().astimezone(EASTERN).hour
                           + k.to_pydatetime().astimezone(EASTERN).minute / 60)
    g["et_hour"] = et.astype("float64")
    known = g["venue"].notna()
    roof = s["roof"].astype(object).str.strip().str.lower()
    g["indoor"] = np.where(roof.isin(INDOORS), 1.0, np.where(roof.isin(OUTDOORS), 0.0, np.nan))
    surface = s["surface"].astype(object).fillna("").astype(str).str.strip().str.lower()
    g["turf"] = np.where(surface.isin(TURF), 1.0, np.where(surface.isin(GRASS), 0.0, np.nan))
    for c in ("indoor", "turf"):
        g.loc[~known, c] = np.nan
    v = g["venue"]
    g["lat"] = v.map(st["lat"]).astype("float64")
    g["lon"] = v.map(st["lon"]).astype("float64")
    g["elev_m"] = v.map(st["elevation_m"]).astype("float64")
    g["venue_tz"] = v.map(st["tz"])
    return g.reset_index(drop=True)


def sides(g: pd.DataFrame) -> pd.DataFrame:
    """Two rows per game, one per team: spread (+ = favoured), total and
    total_rel, implied points, rest for both teams, the Thursday and
    early-kickoff flags, venue facts, and each team's zone shift."""
    tz = team_zones()["tz"]
    base = g.assign(total_rel=g["total_line"] - g["total_center"],
                    thursday=(g["weekday"] == "Thursday").astype(float),
                    early=np.where(g["et_hour"].isna(), np.nan,
                                   (g["et_hour"] <= EARLY_ET_HOUR).astype(float)))
    venue_off = _offset_hours(base["kick_utc"], base["venue_tz"])
    home_off = _offset_hours(base["kick_utc"], base["home_team"].map(tz))
    away_off = _offset_hours(base["kick_utc"], base["away_team"].map(tz))
    keep = ["season", "week", "game_id", "venue", "kick_utc", "et_hour", "month", "weekday",
            "total_line", "total_rel", "thursday", "early", "indoor", "turf", "elev_m"]
    home = base[keep].assign(team=base["home_team"], opponent=base["away_team"], home=1.0,
                             spread=base["spread_line"], rest=base["home_rest"],
                             opp_rest=base["away_rest"], tz_shift=venue_off - home_off,
                             opp_tz_shift=venue_off - away_off)
    away = base[keep].assign(team=base["away_team"], opponent=base["home_team"], home=0.0,
                             spread=-base["spread_line"], rest=base["away_rest"],
                             opp_rest=base["home_rest"], tz_shift=venue_off - away_off,
                             opp_tz_shift=venue_off - home_off)
    out = pd.concat([home, away], ignore_index=True)
    out["implied"] = (out["total_line"] + out["spread"]) / 2
    for c in ("tz_shift", "opp_tz_shift"):
        out[c] = out[c].clip(-TZ_CAP, TZ_CAP)
    return out


# --------------------------------------------------------------------- weather

def _window(kick: pd.Timestamp):
    h0 = kick.floor("h")
    wind = [h0 + pd.Timedelta(hours=i) for i in range(WINDOW_HOURS)]
    rain = [h0 + pd.Timedelta(hours=i + 1) for i in range(WINDOW_HOURS)]
    return h0, wind, rain


def _cache_path(kind: str, venue: str, season: int):
    """One file per venue and season: every hour from the first to the last
    outdoor game's window there (weeks >= PULL_MIN_WEEK)."""
    return OM_CACHE / kind / f"{venue}_{int(season)}.json"


def _request(kind: str, lat: float, lon: float, start: str, end: str) -> tuple[str, dict]:
    params = {"latitude": round(float(lat), 5), "longitude": round(float(lon), 5),
              "start_date": start, "end_date": end,
              "hourly": ",".join(HOURLY), "wind_speed_unit": "mph", "timezone": "GMT",
              "models": FORECAST_MODEL if kind == "forecast" else ARCHIVE_MODEL}
    return (FORECAST_API if kind == "forecast" else ARCHIVE_API), params


@cache
def _hourly(kind: str, venue: str, season: int) -> pd.DataFrame | None:
    """A cached venue-season's hourly series (UTC index: wind mph, rain mm), or None."""
    p = _cache_path(kind, venue, season)
    if not p.exists():
        return None
    blob = json.loads(p.read_text(encoding="utf-8"))
    hourly = (blob.get("response") or {}).get("hourly") or {}
    model = blob["request"]["models"]

    def col(var):
        for k in (var, f"{var}_{model}"):
            if k in hourly:
                return pd.to_numeric(pd.Series(hourly[k], dtype=object), errors="coerce")
        return None
    wind, rain = col("wind_speed_10m"), col("precipitation")
    if not hourly.get("time") or wind is None or rain is None:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(hourly["time"], utc=True))
    return pd.DataFrame({"wind": wind.to_numpy(dtype="float64"),
                         "rain": rain.to_numpy(dtype="float64")}, index=idx)


def _values(kind: str, venue: str, season: int,
            kick: pd.Timestamp) -> tuple[float, float] | None:
    """(wind mean, precipitation sum) over the game's window, or None when any hour is missing."""
    h = _hourly(kind, venue, int(season))
    if h is None:
        return None
    _, wh, rh = _window(kick)
    w = h["wind"].reindex(pd.DatetimeIndex(wh))
    r = h["rain"].reindex(pd.DatetimeIndex(rh))
    if w.isna().any() or r.isna().any():
        return None
    return float(w.mean()), float(r.sum())


def weather(g: pd.DataFrame) -> pd.DataFrame:
    """Per game: wind_kick (mph), precip_kick (mm), wx_source. Indoors 0 and
    0 ('indoors'); outdoors the archived forecast; missing when the venue or
    roof is unknown, nothing is cached, or only the observed reanalysis has
    the game (wx_source 'observed, not used').

    Correction 2026-11-02 (the near-miss bug hunt): the observed reanalysis
    used to stand in where the archived forecast has no values (the five
    2019 international games). It is an observation made after kickoff, so it
    is a leak whatever the pre-registrations disclosed; such a game is now
    unknown and enters the model as 0 plus its `__known` flag."""
    rows = []
    for r in g.itertuples(index=False):
        wind = rain = np.nan
        src = "unknown"
        if r.indoor == 1.0:
            wind, rain, src = 0.0, 0.0, "indoors"
        elif r.indoor == 0.0 and isinstance(r.venue, str) and not pd.isna(r.kick_utc):
            got = _values("forecast", r.venue, r.season, r.kick_utc)
            if got is not None:
                wind, rain = got
                src = "forecast"
            elif _values("archive", r.venue, r.season, r.kick_utc) is not None:
                src = "observed, not used"
            else:
                src = "not cached"
        rows.append({"game_id": r.game_id, "wind_kick": wind, "precip_kick": rain,
                     "wx_source": src})
    return pd.DataFrame(rows, columns=["game_id", "wind_kick", "precip_kick", "wx_source"])


def team_frame(ctx, seasons, with_weather: bool = True) -> pd.DataFrame:
    """sides() with the package's transforms and (unless `with_weather` is
    False) the game's weather, one row per (season, week, team)."""
    g = games(ctx, seasons)
    out = sides(g)
    if with_weather:
        out = out.merge(weather(g), on="game_id", how="left")
        out["wind10"] = (out["wind_kick"] - WIND_FLOOR).clip(lower=0)
        out["precip3h"] = out["precip_kick"].clip(upper=PRECIP_CAP)
    out["rest_c"] = out["rest"].clip(REST_LO, REST_HI)
    out["opp_rest_c"] = out["opp_rest"].clip(REST_LO, REST_HI)
    out["short_rest"] = np.where(out["rest"].isna(), np.nan,
                                 (out["rest"] <= SHORT_REST).astype(float))
    return out.drop_duplicates(["season", "week", "team"]).reset_index(drop=True)


def for_rows(rows: pd.DataFrame, tf: pd.DataFrame, cols) -> pd.DataFrame:
    """The target rows (season, week, team, ...) with `cols` of their team's game."""
    t = rows.copy()
    t["team"] = teams(t["team"])
    return t.merge(tf[["season", "week", "team", *cols]], on=["season", "week", "team"],
                   how="left")


# ------------------------------------------------------------ player history

def aimed_pass(p: pd.DataFrame) -> pd.Series:
    """A pass the quarterback aimed: play_type pass, pass_attempt 1, not a
    sack, not a spike, not a two-point try, air yards recorded."""
    flag = lambda c: pd.to_numeric(p[c], errors="coerce").fillna(0)
    return ((p["play_type"] == "pass") & (flag("pass_attempt") == 1) & (flag("sack") == 0)
            & (flag("qb_spike") == 0) & (flag("two_point_attempt") == 0)
            & p["passer_player_id"].notna()
            & pd.to_numeric(p["air_yards"], errors="coerce").notna())


def deep_share(ctx, keys: pd.DataFrame, role: str) -> pd.DataFrame:
    """Per target row (season, week, gsis_id): `deep_share`, his share of his
    aimed passes (role "passer") or of his targets with air yards (role
    "receiver", the P1a target definition) that travelled DEEP_AIR_YARDS or
    more in the air, season to date (weeks before the row's week), shrunk
    toward `deep_prior` with PSEUDO[role] pseudo-throws. `deep_prior` is the
    league's rate over every such throw of the previous season (no position
    tag decides whose throws count, rule #4). NaN with no earlier throw."""
    seasons = sorted(int(s) for s in keys["season"].unique())
    allseasons = sorted(set(seasons) | {s - 1 for s in seasons})
    pbp = ctx.pbp(allseasons, PASS_COLUMNS)
    air = pd.to_numeric(pbp["air_yards"], errors="coerce")
    if role == "passer":
        mask, who = aimed_pass(pbp), "passer_player_id"
    else:
        mask, who = R.is_target(pbp) & air.notna(), "receiver_player_id"
    ev = pbp.loc[mask, ["season", "week", who]].copy()
    ev["gsis_id"] = C.ids(ev[who])
    ev["aimed"] = 1.0
    ev["deep"] = (air.loc[mask] >= DEEP_AIR_YARDS).astype(float)
    ev = ev.loc[ev["gsis_id"] != ""]
    rate = ev.groupby("season")[["aimed", "deep"]].sum()
    prior = (rate["deep"] / rate["aimed"]).rename("deep_prior").reset_index()
    prior["season"] = prior["season"] + 1
    per = ev.groupby(KEY, as_index=False)[["aimed", "deep"]].sum()
    games_ = C.player_games(ctx, seasons)
    hist = games_.merge(per, on=KEY, how="left").fillna({"aimed": 0.0, "deep": 0.0})
    r = C.as_of(keys[KEY], hist, ["gsis_id"], ["aimed", "deep"])
    r = r.merge(prior, on="season", how="left")
    k = PSEUDO[role]
    r["deep_share"] = (r["deep_std"] + k * r["deep_prior"]) / (r["aimed_std"] + k)
    r.loc[~(r["aimed_std"] > 0), "deep_share"] = np.nan
    return r[[*KEY, "deep_share", "deep_prior", "aimed_std"]]


def last_game_touches(ctx, keys: pd.DataFrame) -> pd.DataFrame:
    """Per target row (season, week, gsis_id, team): `prev_week`, the week of
    his team's previous game this season (the pre-game schedule), and
    `touches_last`, his carries + receptions in that week's box score (0 when
    he has none: he did not play or touched nothing). NaN when the team has
    no earlier game this season."""
    seasons = sorted(int(s) for s in keys["season"].unique())
    tg = C.team_games(ctx, seasons)[["season", "week", "team"]].copy()
    tg["team"] = teams(tg["team"])
    tg = tg.drop_duplicates().rename(columns={"week": "prev_week"})
    tg["week"] = tg["prev_week"]
    t = keys[["season", "week", "gsis_id", "team"]].reset_index(drop=True).copy()
    t["team"] = teams(t["team"])
    t["_order"] = np.arange(len(t))
    t = t.sort_values("week", kind="stable")
    t["week"] = t["week"].astype("int64")
    tg["week"] = tg["week"].astype("int64")
    t["season"] = t["season"].astype("int64")
    tg["season"] = tg["season"].astype("int64")
    t = pd.merge_asof(t, tg.sort_values("week", kind="stable"), on="week",
                      by=["season", "team"], allow_exact_matches=False, direction="backward")
    w = ctx.weekly(seasons)
    box = w[["season", "week", "player_id", "carries", "receptions"]].rename(
        columns={"player_id": "gsis_id", "week": "prev_week"})
    box["gsis_id"] = C.ids(box["gsis_id"])
    box["touches"] = (pd.to_numeric(box["carries"], errors="coerce").fillna(0)
                      + pd.to_numeric(box["receptions"], errors="coerce").fillna(0))
    box = box.groupby(["season", "prev_week", "gsis_id"], as_index=False)["touches"].sum()
    t = t.merge(box, on=["season", "prev_week", "gsis_id"], how="left")
    t["touches_last"] = np.where(t["prev_week"].isna(), np.nan, t["touches"].fillna(0.0))
    t = t.sort_values("_order").drop(columns=["_order", "touches"]).reset_index(drop=True)
    return t[["season", "week", "gsis_id", "prev_week", "touches_last"]]


# ------------------------------------------------------------------------ pull

def _fetch(api: str, params: dict) -> dict:
    """One Open-Meteo answer. The session's egress rotates over several
    addresses and some reach a daily limit first, so a refusal for a limit
    or a dropped connection is retried after a short, growing pause."""
    import requests
    for attempt in range(1, 301):
        wait = min(3.0 * attempt, 120.0)
        try:
            r = requests.get(api, params=params, timeout=90)
            d = r.json()
        except (requests.RequestException, ValueError) as exc:   # proxy, TLS, bad JSON
            if attempt % 10 == 1:
                print(f"    retry {attempt} in {wait:.0f}s: {str(exc)[:90]}", flush=True)
            time.sleep(wait)
            continue
        if isinstance(d, dict) and d.get("error"):
            reason = str(d.get("reason", ""))
            if "limit" in reason.lower():
                if attempt % 10 == 1:
                    print(f"    retry {attempt} in {wait:.0f}s: {reason[:60]}", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Open-Meteo refused {params}: {reason}")
        return d
    raise RuntimeError(f"Open-Meteo did not answer for {params}")


def _bare_context():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "research"))
    import block_runner as BR
    return BR.Context(pd.DataFrame(columns=["position"]), None)


def _spans(g: pd.DataFrame, min_week: int) -> list[dict]:
    """Per (venue, season) with an outdoor game in weeks >= min_week: the
    coordinates, the UTC dates covering every such game's window, the kickoffs."""
    need = g.loc[(g["week"] >= min_week) & (g["indoor"] == 0.0) & g["venue"].notna()
                 & g["kick_utc"].notna()]
    out = []
    for (venue, season), grp in need.groupby(["venue", "season"], sort=True):
        kicks = list(grp["kick_utc"])
        out.append({"venue": venue, "season": int(season), "lat": float(grp["lat"].iloc[0]),
                    "lon": float(grp["lon"].iloc[0]),
                    "start": min(k.floor("h") for k in kicks).strftime("%Y-%m-%d"),
                    "end": max(_window(k)[2][-1] for k in kicks).strftime("%Y-%m-%d"),
                    "kicks": kicks})
    return out


def _save(kind: str, sp: dict) -> None:
    path = _cache_path(kind, sp["venue"], sp["season"])
    if path.exists():
        return
    api, params = _request(kind, sp["lat"], sp["lon"], sp["start"], sp["end"])
    d = _fetch(api, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({
        "source": "Open-Meteo.com (CC BY 4.0)", "api": api, "request": params,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "response": d}), encoding="utf-8")
    tmp.replace(path)


def pull(seasons, min_week: int = PULL_MIN_WEEK, workers: int = 4) -> dict:
    """Download (resumably) every outdoor venue-season's archived forecast,
    then the observed reanalysis for the venue-seasons where the forecast
    leaves any game's window without values."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    spans = _spans(games(_bare_context(), seasons), min_week)

    def run(kind, todo):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_save, kind, sp) for sp in todo]
            for i, f in enumerate(as_completed(futs), 1):
                f.result()
                if i % 25 == 0 or i == len(futs):
                    print(f"  {kind}: {i}/{len(futs)} venue-seasons", flush=True)
        _hourly.cache_clear()
    run("forecast", spans)
    lacking = [sp for sp in spans if any(
        _values("forecast", sp["venue"], sp["season"], k) is None for k in sp["kicks"])]
    run("archive", lacking)
    counts = {"venue_seasons": len(spans), "archive_venue_seasons": len(lacking),
              "games": sum(len(sp["kicks"]) for sp in spans), "forecast": 0, "observed": 0,
              "missing": 0}
    for sp in spans:
        for k in sp["kicks"]:
            if _values("forecast", sp["venue"], sp["season"], k) is not None:
                counts["forecast"] += 1
            elif _values("archive", sp["venue"], sp["season"], k) is not None:
                counts["observed"] += 1
            else:
                counts["missing"] += 1
    print(f"done: {counts}", flush=True)
    return counts


def summary(seasons, min_week: int = PULL_MIN_WEEK) -> pd.DataFrame:
    """Games by season and weather source (weeks >= min_week); no scores."""
    g = games(_bare_context(), seasons)
    g = g.loc[g["week"] >= min_week]
    w = weather(g)
    tab = pd.crosstab(g["season"].to_numpy(), w["wx_source"].to_numpy())
    print(tab.to_string())
    return tab


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P5: pull Open-Meteo weather for outdoor games")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--seasons", default="2019-2025")
    args = ap.parse_args(argv)
    a, _, b = args.seasons.partition("-")
    seasons = list(range(int(a), int(b or a) + 1))
    if args.pull:
        pull(seasons)
    if args.summary:
        summary(seasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
