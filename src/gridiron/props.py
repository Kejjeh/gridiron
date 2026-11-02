"""Player-prop markets: parse, de-vig, consensus, expected stats, the market
projection, and the odds-name identity table.

Pure: no network, no clock, no writes. `scripts/ingest/pull_props.py`
fetches and stores; this module only turns a stored response into numbers.
The full design, the credit math and the fitted parameters' evidence are in
docs/research/PROPS_COLLECTOR.md.

Owner decisions of 2026-11-02 (docs/research/MODEL_ROADMAP.md, "Decisions"):

1. Odds providers send NAMES, not ids. A name resolves only through the
   committed table data/reference/odds_player_names.csv (`IdentityTable`),
   keyed by (name exactly as sent, nflverse team); every later join is on
   gsis_id (rule #3). An unmatched name abstains and is counted, never
   guessed. 'exact' rows are machine-made (`scripts/ingest/odds_names.py`);
   anything that SERVES a number needs a row a person marked 'reviewed'
   (`IdentityTable.serving_id` refuses the rest). Nothing serves yet.
2. Raw odds and per-book lines stay private: the provider's terms forbid
   redistributing its data. Only values derived here (an expected stat line
   per player, the market projection, aggregate grades) may be public.
3. No serving change after the week-14 board. 4. No scraping: the
   provider's sanctioned free API tier only.

What is computed, in order:

* de-vig, per book: each two-way market's implied probabilities are scaled
  to sum to one (multiplicative). An anytime-TD "Yes" offered alone is
  scaled by that book's own median overround over its two-way props in the
  same snapshot; a book with no two-way prop there abstains on it.
* consensus per (player, market): the median across books of the line and
  of the de-vigged P(over), and the number of books.
* expected stat from (line, P(over)): yardage and kicking points through a
  gamma with one fitted shape per stat, solved so P(X > line) = P(over);
  counts through a negative binomial with one fitted size per stat (a
  Poisson where history shows no overdispersion), solved the same way; the
  anytime TD through lambda = -ln(1 - P(yes)). The shapes and sizes are
  fitted on 2019-2025 by scripts/research/prop_distribution_fit.py and
  committed with their evidence in props_params.json beside this module.
* the market projection: the league's scoring is linear (half-PPR, no
  bonuses), so E[points] = points(E[stats]), scored by gridiron.scoring
  (rule #2). Stats no market covers come from the player's own per-game
  rate this season, and the record says which; a player whose
  position-core market is missing abstains.

Record-only. Nothing reads these numbers to project, rank, gate or
recommend: `gridiron.dashboard.contenders_block` records them as the
`market_v1` contender for the weekly grade and nothing else.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from gridiron.league_config import DEFAULT_SCORING, KICKING_SCORING, ScoringRules
from gridiron.paths import REFERENCE, RESEARCH_CACHE
from gridiron.scoring import fantasy_points, kicker_points

#: Fitted distribution parameters and their evidence (committed).
PARAMS_PATH = Path(__file__).with_name("props_params.json")
#: The committed identity table (decision 1).
IDENTITY_PATH = REFERENCE / "odds_player_names.csv"
#: Local, gitignored storage. raw/ holds provider responses and the per-book
#: table and never leaves the machine unencrypted; derived/ holds only what
#: this module derives from them.
PROPS_ROOT = RESEARCH_CACHE / "props"
RAW_ROOT = PROPS_ROOT / "raw"
DERIVED_ROOT = PROPS_ROOT / "derived"

#: The Odds API's full team names -> nflverse abbreviations (32 rows).
TEAM_ABBR: dict[str, str] = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
NFL_TEAMS: frozenset[str] = frozenset(TEAM_ABBR.values())

GAMMA, COUNT, ANYTIME = "gamma", "count", "anytime_td"


@dataclass(frozen=True)
class Market:
    """One provider market: the stat it settles on and how it is modelled."""

    key: str
    #: the expected-stat name: the nflverse weekly column where one exists
    #: ('scrimmage_tds' = rushing + receiving TDs; 'kicking_points' = the
    #: books' 3 per field goal + 1 per extra point)
    stat: str
    family: str
    #: roster positions consistent with the market (the identity check only;
    #: a player's role is never read from a position tag, rule #4)
    positions: frozenset[str]


_PASSER = frozenset({"QB"})
_RUSHER = frozenset({"QB", "RB", "FB", "WR", "TE"})
_CATCHER = frozenset({"RB", "FB", "WR", "TE"})
_KICKER = frozenset({"K"})

MARKETS: dict[str, Market] = {m.key: m for m in (
    Market("player_pass_yds", "passing_yards", GAMMA, _PASSER),
    Market("player_pass_tds", "passing_tds", COUNT, _PASSER),
    Market("player_pass_attempts", "attempts", COUNT, _PASSER),
    Market("player_pass_completions", "completions", COUNT, _PASSER),
    Market("player_pass_interceptions", "passing_interceptions", COUNT, _PASSER),
    Market("player_rush_yds", "rushing_yards", GAMMA, _RUSHER),
    Market("player_rush_attempts", "carries", COUNT, _RUSHER),
    Market("player_receptions", "receptions", COUNT, _CATCHER),
    Market("player_reception_yds", "receiving_yards", GAMMA, _CATCHER),
    Market("player_anytime_td", "scrimmage_tds", ANYTIME, _RUSHER),
    Market("player_kicking_points", "kicking_points", GAMMA, _KICKER),
    Market("player_field_goals", "fg_made", COUNT, _KICKER),
    Market("player_pats", "pat_made", COUNT, _KICKER),
)}

#: Bought for every due game: at most 6 credits on one region.
CORE_MARKETS: tuple[str, ...] = ("player_pass_yds", "player_pass_tds", "player_rush_yds",
                                 "player_receptions", "player_reception_yds",
                                 "player_anytime_td")
#: Bought only with slack above the month's core reserve.
SECONDARY_MARKETS: tuple[str, ...] = ("player_kicking_points", "player_pass_attempts",
                                      "player_pass_completions",
                                      "player_pass_interceptions", "player_rush_attempts")
REGIONS: tuple[str, ...] = ("us",)

#: The market a position cannot be projected without (any one of them).
POSITION_CORE: dict[str, tuple[str, ...]] = {
    "QB": ("passing_yards",), "RB": ("rushing_yards",),
    "WR": ("receptions", "receiving_yards"), "TE": ("receptions", "receiving_yards"),
    "K": ("kicking_points",),
}
#: The books' kicking-points definition (3 per field goal, 1 per extra point,
#: no distance bonus). Standard at US books; UNVERIFIED for each book. It is
#: the MARKET's definition, used only to read the market; the league's kicker
#: points come from `gridiron.scoring.kicker_points` (rule #2).
BOOK_KICKING_POINTS = {"fg": 3.0, "pat": 1.0}

#: A de-vigged main line outside this band is not believed (an alternate or
#: a stale line); the market abstains rather than extrapolate a tail.
P_BAND = (0.05, 0.95)
#: Anytime-TD probabilities run lower (a backup tight end's "Yes").
P_BAND_ANYTIME = (0.005, 0.95)

_FG_MADE = ("fg_made_0_19", "fg_made_20_29", "fg_made_30_39", "fg_made_40_49",
            "fg_made_50_59", "fg_made_60_")
#: Per-game rates the projection may read (current nflverse names, then the
#: pre-rewrite aliases `gridiron.scoring` still accepts).
RATE_COLUMNS: tuple[str, ...] = (
    "passing_yards", "passing_tds", "passing_interceptions", "rushing_yards",
    "rushing_tds", "receptions", "receiving_yards", "receiving_tds",
    "sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost",
    "passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions",
    "interceptions", "fumbles_lost", "two_point_conversions",
    *_FG_MADE, "pat_made", "fg_missed", "pat_missed",
)
#: The offensive stats a market can set; everything else comes from rates.
_MARKET_STATS = ("passing_yards", "passing_tds", "passing_interceptions", "rushing_yards",
                 "receptions", "receiving_yards")
#: Market stats that move league points ('all' = the whole entry was set aside).
_SCORED_MARKET_STATS = frozenset({*_MARKET_STATS, "scrimmage_tds", "kicking_points", "all"})


# ======================================================================
# distributions (pure Python: the runtime has no scipy)
# ======================================================================

def _gamma_series(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) by its series (x < a + 1)."""
    ap, total, term = a, 1.0 / a, 1.0 / a
    for _ in range(2000):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-16:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_cf(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) by Lentz's continued
    fraction (x >= a + 1)."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 2000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gamma_p(a: float, x: float) -> float:
    """P(a, x): the regularized lower incomplete gamma function."""
    if a <= 0:
        raise ValueError("gamma shape must be positive")
    if x <= 0:
        return 0.0
    return _gamma_series(a, x) if x < a + 1.0 else 1.0 - _gamma_cf(a, x)


def gamma_q(a: float, x: float) -> float:
    """Q(a, x) = 1 - P(a, x), computed on its own side for accuracy."""
    if a <= 0:
        raise ValueError("gamma shape must be positive")
    if x <= 0:
        return 1.0
    return 1.0 - _gamma_series(a, x) if x < a + 1.0 else _gamma_cf(a, x)


def gamma_q_inverse(a: float, p: float) -> float:
    """The x with Q(a, x) = p (bisection; Q falls from 1 to 0 in x)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be inside (0, 1)")
    lo, hi = 0.0, max(1.0, a)
    while gamma_q(a, hi) > p:
        lo, hi = hi, hi * 2.0
        if hi > 1e9:
            raise ValueError("gamma quantile did not bracket")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if gamma_q(a, mid) > p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-13 * hi:
            break
    return 0.5 * (lo + hi)


def gamma_unit_median(shape: float) -> float:
    """The median of a gamma with mean 1 and the given shape."""
    return gamma_q_inverse(shape, 0.5) / shape


def count_pmf(n: int, mu: float, size: float | None) -> float:
    """P(X = n) for a negative binomial with mean `mu` and size `size`
    (variance mu + mu^2 / size), or a Poisson when `size` is None."""
    if n < 0:
        return 0.0
    if mu <= 0:
        return 1.0 if n == 0 else 0.0
    if size is None:
        return math.exp(n * math.log(mu) - mu - math.lgamma(n + 1))
    r = float(size)
    return math.exp(math.lgamma(n + r) - math.lgamma(r) - math.lgamma(n + 1)
                    + r * math.log(r / (r + mu)) + n * math.log(mu / (r + mu)))


def count_cdf(n: int, mu: float, size: float | None) -> float:
    """P(X <= n)."""
    if n < 0:
        return 0.0
    if size is None and mu > 0:
        return gamma_q(n + 1.0, mu)        # the Poisson-gamma identity
    return min(1.0, sum(count_pmf(k, mu, size) for k in range(int(n) + 1)))


def count_over(line: float, mu: float, size: float | None) -> float:
    """P(over `line`) as a book settles it: a whole-number line pushes on
    equality, so the push is taken out (P(X > L) / (1 - P(X = L)))."""
    if float(line).is_integer():
        n = int(line)
        above = 1.0 - count_cdf(n, mu, size)
        below = count_cdf(n - 1, mu, size)
        tot = above + below
        return above / tot if tot > 0 else 0.0
    return 1.0 - count_cdf(math.floor(line), mu, size)


def count_mean(line: float, p_over: float, size: float | None) -> float:
    """The mean whose P(over `line`) is `p_over` (bisection on log mean;
    P(over) rises with the mean)."""
    lo, hi = math.log(1e-6), math.log(500.0)
    if not count_over(line, math.exp(lo), size) < p_over < count_over(line, math.exp(hi), size):
        raise ValueError("p_over is outside what a count model can produce at this line")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if count_over(line, math.exp(mid), size) < p_over:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return math.exp(0.5 * (lo + hi))


def gamma_mean(line: float, p_over: float, shape: float) -> float:
    """The mean of the gamma with this shape whose P(X > line) is p_over."""
    return shape * line / gamma_q_inverse(shape, p_over)


def anytime_lambda(p_yes: float) -> float:
    """Expected touchdowns from P(at least one): lambda = -ln(1 - p)."""
    return -math.log(1.0 - p_yes)


# ======================================================================
# fitted parameters
# ======================================================================

@dataclass(frozen=True)
class PropParams:
    """One gamma shape per yardage stat and one negative-binomial size per
    count stat (None = Poisson), as committed in props_params.json."""

    gamma_shape: Mapping[str, float]
    count_size: Mapping[str, float | None]
    fitted: str = ""

    @classmethod
    def load(cls, path: Path = PARAMS_PATH) -> "PropParams":
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        shapes = {k: float(v["shape"]) for k, v in (blob.get("gamma") or {}).items()}
        sizes: dict[str, float | None] = {}
        for k, v in (blob.get("counts") or {}).items():
            size = v.get("size")
            sizes[k] = None if size is None else float(size)
        if not shapes or not sizes:
            raise ValueError(f"{path}: no fitted parameters")
        return cls(shapes, sizes, str((blob.get("meta") or {}).get("fitted") or ""))


def expected_stat(market: str, line: float | None, p_over: float,
                  params: PropParams) -> tuple[float | None, str]:
    """(expected value, '') or (None, why it abstains)."""
    spec = MARKETS.get(market)
    if spec is None:
        return None, "market not catalogued"
    if spec.family == ANYTIME:
        lo, hi = P_BAND_ANYTIME
        if not lo <= p_over <= hi:
            return None, f"anytime-TD probability {p_over:.3f} outside {lo}-{hi}"
        return anytime_lambda(p_over), ""
    lo, hi = P_BAND
    if line is None:
        return None, "no line"
    if not lo <= p_over <= hi:
        return None, f"P(over) {p_over:.3f} outside {lo}-{hi}"
    if spec.family == GAMMA:
        shape = params.gamma_shape.get(spec.stat)
        if shape is None:
            return None, f"no fitted gamma shape for {spec.stat}"
        if line <= 0:
            return None, "a gamma cannot settle a line at or below zero"
        return gamma_mean(float(line), p_over, shape), ""
    if spec.stat not in params.count_size:
        return None, f"no fitted count model for {spec.stat}"
    if line < 0:
        return None, "negative count line"
    try:
        return count_mean(float(line), p_over, params.count_size[spec.stat]), ""
    except ValueError as exc:
        return None, str(exc)


# ======================================================================
# parsing and de-vig
# ======================================================================

@dataclass(frozen=True)
class PropRow:
    """One outcome of one player market at one book, as the provider sent it."""

    event_id: str
    commence_time: str
    home: str            # nflverse abbreviation; '' when the name is not one of the 32
    away: str
    book: str
    market: str
    player: str          # outcome.description EXACTLY as sent (never normalised)
    side: str            # Over | Under | Yes | No
    point: float | None
    price: float         # American odds


_SIDES = {"over": "Over", "under": "Under", "yes": "Yes", "no": "No"}


def implied_probability(price: float) -> float:
    """The probability an American price implies, margin included."""
    p = float(price)
    if p >= 100:
        return 100.0 / (p + 100.0)
    if p <= -100:
        return -p / (-p + 100.0)
    raise ValueError("an American price is never strictly between -100 and +100")


def event_teams(payload: Mapping[str, object]) -> tuple[str, str]:
    """(home, away) as nflverse abbreviations ('' for an unknown name)."""
    return (TEAM_ABBR.get(str(payload.get("home_team") or ""), ""),
            TEAM_ABBR.get(str(payload.get("away_team") or ""), ""))


def parse_event_odds(payload: object) -> tuple[list[PropRow], Counter]:
    """Rows from one event-odds response; `skipped` counts what was not usable."""
    skipped: Counter = Counter()
    rows: list[PropRow] = []
    if not isinstance(payload, Mapping):
        skipped["response is not an event object"] += 1
        return rows, skipped
    home, away = event_teams(payload)
    event_id = str(payload.get("id") or "")
    commence = str(payload.get("commence_time") or "")
    for book in payload.get("bookmakers") or []:
        if not isinstance(book, Mapping) or not book.get("key"):
            skipped["bookmaker without a key"] += 1
            continue
        for market in book.get("markets") or []:
            key = str((market or {}).get("key") or "") if isinstance(market, Mapping) else ""
            if key not in MARKETS:
                skipped["market not catalogued"] += 1
                continue
            for o in market.get("outcomes") or []:
                if not isinstance(o, Mapping):
                    skipped["malformed outcome"] += 1
                    continue
                player = o.get("description")
                side = _SIDES.get(str(o.get("name") or "").strip().lower())
                if not isinstance(player, str) or not player or side is None:
                    skipped["outcome without a player or side"] += 1
                    continue
                try:
                    price = float(o.get("price"))
                    implied_probability(price)
                    point = None if o.get("point") is None else float(o.get("point"))
                except (TypeError, ValueError):
                    skipped["unreadable price or point"] += 1
                    continue
                if (point is not None and not math.isfinite(point)) or not math.isfinite(price):
                    skipped["unreadable price or point"] += 1
                    continue
                rows.append(PropRow(event_id, commence, home, away, str(book["key"]), key,
                                    player, side, point, price))
    return rows, skipped


@dataclass(frozen=True)
class BookLine:
    """One book's de-vigged main line for one player market."""

    event_id: str
    book: str
    market: str
    player: str
    point: float | None
    p_over: float              # de-vigged P(over); P(yes) for the anytime TD
    overround: float | None    # q_over + q_under - 1 (None when one-sided)
    method: str                # "two-way" | "one-sided"


def devig(rows: Iterable[PropRow]) -> tuple[list[BookLine], Counter]:
    """Each book's two-way markets normalised to sum to one; the line whose
    P(over) is nearest 0.5 kept as that book's main line. A one-sided
    anytime-TD Yes is scaled by the book's median two-way overround in the
    same snapshot, or dropped (counted) when the book has none there."""
    dropped: Counter = Counter()
    groups: dict[tuple[str, str, str, str], list[PropRow]] = {}
    for r in rows:
        groups.setdefault((r.event_id, r.book, r.market, r.player), []).append(r)

    pairs: dict[tuple[str, str, str, str], list[BookLine]] = {}
    pending_yes: list[tuple[tuple[str, str, str, str], PropRow]] = []
    for key, members in groups.items():
        event_id, book, market, player = key
        if MARKETS[market].family == ANYTIME:
            yes = [m for m in members if m.side == "Yes"]
            no = [m for m in members if m.side == "No"]
            if len(yes) != 1 or len(no) > 1:
                dropped["anytime TD without exactly one Yes"] += 1
                continue
            if no:
                qy, qn = implied_probability(yes[0].price), implied_probability(no[0].price)
                pairs[key] = [BookLine(event_id, book, market, player, None,
                                       qy / (qy + qn), qy + qn - 1.0, "two-way")]
            else:
                pending_yes.append((key, yes[0]))
            continue
        by_point: dict[float | None, list[PropRow]] = {}
        for m in members:
            by_point.setdefault(m.point, []).append(m)
        found = []
        for point, sides in by_point.items():
            over = [s for s in sides if s.side == "Over"]
            under = [s for s in sides if s.side == "Under"]
            if point is None or len(over) != 1 or len(under) != 1 or len(sides) != 2:
                dropped["over/under not offered as one clean pair"] += 1
                continue
            qo, qu = implied_probability(over[0].price), implied_probability(under[0].price)
            found.append(BookLine(event_id, book, market, player, point,
                                  qo / (qo + qu), qo + qu - 1.0, "two-way"))
        if found:
            pairs[key] = found

    overrounds: dict[tuple[str, str], list[float]] = {}
    for (event_id, book, _m, _p), found in pairs.items():
        overrounds.setdefault((event_id, book), []).extend(
            b.overround for b in found if b.overround is not None)

    out: list[BookLine] = []
    for found in pairs.values():
        main = min(found, key=lambda b: (abs(b.p_over - 0.5), b.point or 0.0))
        if len(found) > 1:
            dropped["alternate line set aside (main line kept)"] += len(found) - 1
        out.append(main)
    for (event_id, book, market, player), yes in pending_yes:
        book_or = overrounds.get((event_id, book)) or []
        if not book_or:
            dropped["anytime TD Yes only, book has no two-way prop to de-vig it"] += 1
            continue
        p = implied_probability(yes.price) / (1.0 + statistics.median(book_or))
        out.append(BookLine(event_id, book, market, player, None, p, None, "one-sided"))
    out.sort(key=lambda b: (b.event_id, b.market, b.player, b.book))
    return out, dropped


@dataclass(frozen=True)
class Consensus:
    """The market's view of one player market across books."""

    event_id: str
    market: str
    player: str
    line: float | None         # median across books (None for the anytime TD)
    p_over: float              # median across books of the de-vigged P(over)
    books: int
    methods: tuple[str, ...]


def consensus(lines: Iterable[BookLine]) -> dict[tuple[str, str, str], Consensus]:
    """(event_id, player as sent, market) -> Consensus."""
    groups: dict[tuple[str, str, str], list[BookLine]] = {}
    for b in lines:
        groups.setdefault((b.event_id, b.player, b.market), []).append(b)
    out = {}
    for (event_id, player, market), members in groups.items():
        points = [b.point for b in members if b.point is not None]
        out[(event_id, player, market)] = Consensus(
            event_id, market, player,
            float(statistics.median(points)) if points else None,
            float(statistics.median(b.p_over for b in members)),
            len({b.book for b in members}),
            tuple(sorted({b.method for b in members})))
    return out


# ======================================================================
# identity (decision 1, rule #3)
# ======================================================================

IDENTITY_COLUMNS: tuple[str, ...] = ("odds_name", "team", "gsis_id", "basis", "added")
EXACT, REVIEWED = "exact", "reviewed"
BASES = (EXACT, REVIEWED)
_GSIS = re.compile(r"^00-\d{7}$")


class UnreviewedIdentity(PermissionError):
    """A serving path asked for an identity no person has reviewed."""


@dataclass(frozen=True)
class IdentityRow:
    odds_name: str
    team: str
    gsis_id: str
    basis: str
    added: str


@dataclass(frozen=True)
class Resolved:
    """How one name on one event resolved. `gsis_id` None means it abstains."""

    gsis_id: str | None
    team: str = ""
    basis: str = ""
    reason: str = ""          # 'unmatched' | 'ambiguous' when gsis_id is None


class IdentityTable:
    """(odds name exactly as sent, nflverse team) -> gsis_id. The ONLY way an
    odds name becomes an id; joins downstream use the id alone."""

    def __init__(self, rows: Iterable[IdentityRow] = ()) -> None:
        index: dict[tuple[str, str], IdentityRow] = {}
        for r in rows:
            problem = self.problem(r)
            if problem:
                raise ValueError(f"odds name table row {r!r}: {problem}")
            key = (r.odds_name, r.team)
            prior = index.get(key)
            if prior is not None and prior.gsis_id != r.gsis_id:
                raise ValueError(f"odds name table maps {r.odds_name!r} on {r.team} to two "
                                 f"players ({prior.gsis_id}, {r.gsis_id})")
            if prior is None or r.basis == REVIEWED:
                index[key] = r
        self._index = index

    @staticmethod
    def problem(r: IdentityRow) -> str:
        if not r.odds_name or r.odds_name != r.odds_name.strip("\r\n"):
            return "empty or broken odds_name"
        if r.team not in NFL_TEAMS:
            return f"team {r.team!r} is not an nflverse abbreviation"
        if not _GSIS.match(r.gsis_id):
            return f"gsis_id {r.gsis_id!r} is not a gsis id"
        if r.basis not in BASES:
            return f"basis {r.basis!r} is not one of {BASES}"
        try:
            date.fromisoformat(r.added)
        except ValueError:
            return f"added {r.added!r} is not a YYYY-MM-DD date"
        return ""

    @classmethod
    def read(cls, path: Path = IDENTITY_PATH) -> "IdentityTable":
        with Path(path).open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if tuple(reader.fieldnames or ()) != IDENTITY_COLUMNS:
                raise ValueError(f"{path}: header must be {','.join(IDENTITY_COLUMNS)}")
            return cls(IdentityRow(*(row[c] or "" for c in IDENTITY_COLUMNS)) for row in reader)

    def __len__(self) -> int:
        return len(self._index)

    def rows(self) -> tuple[IdentityRow, ...]:
        return tuple(self._index.values())

    def has(self, odds_name: str, team: str) -> bool:
        return (odds_name, team) in self._index

    def resolve(self, odds_name: str, teams: Iterable[str]) -> Resolved:
        """The row for this name on one of the event's two teams. Record-only
        callers may use an 'exact' row; serving callers go through
        `serving_id`."""
        hits = {t: self._index[(odds_name, t)] for t in set(teams) if (odds_name, t) in self._index}
        ids = {r.gsis_id for r in hits.values()}
        if not ids:
            return Resolved(None, reason="unmatched")
        if len(ids) > 1:
            return Resolved(None, reason="ambiguous")
        row = max(hits.values(), key=lambda r: (r.basis == REVIEWED, r.team))
        return Resolved(row.gsis_id, row.team, row.basis)

    def serving_id(self, odds_name: str, teams: Iterable[str]) -> str:
        """The gsis id a SERVING path may use: only a row a person reviewed.
        There is no serving path yet; this is the gate one must pass."""
        r = self.resolve(odds_name, teams)
        if r.gsis_id is None:
            raise LookupError(f"odds name {r.reason}: nothing to serve")
        if r.basis != REVIEWED:
            raise UnreviewedIdentity(
                "this odds name resolves through an 'exact' (machine) row; a served "
                "number needs the owner's review of the table (basis 'reviewed')")
        return r.gsis_id


# ======================================================================
# one snapshot -> the public derived entry for its game
# ======================================================================

def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(timezone.utc).isoformat(timespec="seconds")


def derive_game(payload: object, *, table: IdentityTable, params: PropParams,
                taken_at: datetime, kickoff: datetime | None) -> dict:
    """The derived, public entry for one game's snapshot: per resolved player
    (by gsis id) the expected stat line and the number of books behind each
    stat; unmatched and ambiguous names COUNTED, never named; no line, price
    or book appears."""
    rows, skipped = parse_event_odds(payload)
    lines, dropped = devig(rows)
    cons = consensus(lines)
    home, away = event_teams(payload if isinstance(payload, Mapping) else {})
    teams = tuple(t for t in (home, away) if t)
    if len(teams) < 2:
        skipped["event team name not in the 32-team map"] += 2 - len(teams)
    players: dict[str, dict] = {}
    unmatched: set[str] = set()
    ambiguous: set[str] = set()
    names_of: dict[str, set[str]] = {}
    abstained: Counter = Counter()
    for (_e, name, market), c in sorted(cons.items()):
        res = table.resolve(name, teams)
        if res.gsis_id is None:
            (unmatched if res.reason == "unmatched" else ambiguous).add(name)
            continue
        names_of.setdefault(res.gsis_id, set()).add(name)
        entry = players.setdefault(res.gsis_id, {"team": res.team, "basis": res.basis,
                                                 "stats": {}, "books": {}, "abstained": {}})
        if res.basis == EXACT:
            entry["basis"] = EXACT            # the weakest basis behind any of his stats
        stat = MARKETS[market].stat
        value, why = expected_stat(market, c.line, c.p_over, params)
        if value is None:
            entry["abstained"][stat] = why
            abstained[stat] += 1
            continue
        entry["stats"][stat] = round(value, 4)
        entry["books"][stat] = c.books
        if MARKETS[market].family == ANYTIME:
            entry["td_method"] = "+".join(c.methods)
    # A player two different odds names resolve to is not trusted on any stat
    # both names priced: which name is right is a question for the table.
    for gid, names in names_of.items():
        if len(names) > 1:
            players[gid]["abstained"]["all"] = "two odds names resolve to this player"
            players[gid]["stats"] = {}
    # An anytime TD a resolved player was offered but no book could de-vig.
    offered_td = {r.player for r in rows if MARKETS[r.market].family == ANYTIME}
    priced_td = {name for (_e, name, m) in cons if MARKETS[m].family == ANYTIME}
    for name in offered_td - priced_td:
        res = table.resolve(name, teams)
        if res.gsis_id is not None and res.gsis_id in players:
            players[res.gsis_id]["abstained"]["scrimmage_tds"] = (
                "anytime TD offered Yes-only by books with no two-way prop to de-vig it")
            abstained["scrimmage_tds"] += 1
    lead = None
    if kickoff is not None:
        lead = round((kickoff - taken_at).total_seconds() / 60.0, 1)
    return {
        "taken_at": _iso(taken_at), "kickoff": _iso(kickoff), "lead_minutes": lead,
        "teams": sorted(teams),
        "books": len({r.book for r in rows}),
        "markets_returned": sorted({r.market for r in rows}),
        "players": {gid: players[gid] for gid in sorted(players)},
        "unmatched": len(unmatched), "ambiguous": len(ambiguous),
        "abstained": dict(sorted(abstained.items())),
        "skipped": dict(sorted((skipped + dropped).items())),
        "params_fitted": params.fitted,
    }


# ======================================================================
# the raw archive (PRIVATE: provider responses; read here, written by the
# collector)
# ======================================================================

def has_odds(payload: object) -> bool:
    """A response with at least one priced market (an empty one is free and
    is not a snapshot)."""
    return isinstance(payload, Mapping) and any(
        (b or {}).get("markets") for b in payload.get("bookmakers") or [] if isinstance(b, Mapping))


def read_raw_snapshots(raw_root: Path, season: int) -> dict[tuple[int, str], tuple[Path, dict]]:
    """(week, nflverse game_id) -> (path, {"meta", "payload"}) for the
    EARLIEST snapshot of each game that has odds in it: one snapshot per
    game is the rule, and an earlier empty response does not count."""
    out: dict[tuple[int, str], tuple[Path, dict]] = {}
    base = Path(raw_root) / f"season{int(season)}"
    for path in sorted(base.glob("week*/*__*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            meta = blob["meta"]
            week, gid = int(meta["week"]), str(meta["game_id"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not has_odds(blob.get("payload")) or (week, gid) in out:
            continue
        out[(week, gid)] = (path, blob)
    return out


#: Local-only name lists (`pull_props.py names`): one cheap market per game,
#: read by `scripts/ingest/odds_names.py propose`, NEVER a snapshot (they
#: sit outside season<YYYY>/ so no snapshot reader can see them).
NAMES_DIR = "names"


def read_name_files(raw_root: Path, season: int) -> dict[tuple[int, str], list[tuple[Path, dict]]]:
    """(week, nflverse game_id) -> every local name-list response with odds
    in it, oldest first."""
    out: dict[tuple[int, str], list[tuple[Path, dict]]] = {}
    base = Path(raw_root) / NAMES_DIR / f"season{int(season)}"
    for path in sorted(base.glob("week*/*__*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            meta = blob["meta"]
            week, gid = int(meta["week"]), str(meta["game_id"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if has_odds(blob.get("payload")):
            out.setdefault((week, gid), []).append((path, blob))
    return out


# ======================================================================
# the derived week file (public: derived values only)
# ======================================================================

DERIVED_KIND = "gridiron.props.derived"
DERIVED_VERSION = 1


def derived_path(root: Path, season: int, week: int) -> Path:
    return Path(root) / f"season{int(season)}" / f"week{int(week):02d}.json"


def empty_week(season: int, week: int) -> dict:
    return {"kind": DERIVED_KIND, "version": DERIVED_VERSION, "season": int(season),
            "week": int(week), "games": {},
            "note": ("derived from player-prop markets for grading only: expected stat "
                     "lines per player and counts; no line, price or book")}


def read_derived(root: Path, season: int, week: int) -> dict | None:
    """The week's derived file, or None when absent, unreadable or not for
    this season and week (a restored file is input, not truth)."""
    path = derived_path(root, season, week)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(blob, dict) or blob.get("kind") != DERIVED_KIND
            or blob.get("version") != DERIVED_VERSION
            or blob.get("season") != int(season) or blob.get("week") != int(week)
            or not isinstance(blob.get("games"), dict)):
        return None
    return blob


# ======================================================================
# the market projection (board time, record-only)
# ======================================================================

def _ts(text: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class MarketProjection:
    """The market's expected league points for one player, or why not."""

    points: float | None
    reason: str = ""
    market_stats: tuple[str, ...] = ()
    #: stats taken from his own per-game rate this season (nonzero ones)
    rate_stats: tuple[str, ...] = ()
    books: int = 0
    basis: str = ""
    taken_at: str = ""


def season_rates(frame, through_week: int | None) -> dict[str, dict[str, float]]:
    """gsis_id -> {column: per-game mean} over this season's weeks up to
    `through_week`, for the columns in RATE_COLUMNS the frame carries. A
    player with no game in that span is absent (his rates are UNKNOWN)."""
    if frame is None or through_week is None or len(frame) == 0 or "gsis_id" not in frame:
        return {}
    import pandas as pd
    f = frame.loc[pd.to_numeric(frame["week"], errors="coerce") <= int(through_week)]
    cols = [c for c in RATE_COLUMNS if c in f.columns]
    if not len(f):
        return {}
    num = f[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    num["gsis_id"] = f["gsis_id"].astype(str)
    num["_week"] = pd.to_numeric(f["week"], errors="coerce")
    out: dict[str, dict[str, float]] = {}
    for gid, g in num.groupby("gsis_id"):
        games = int(g["_week"].nunique())
        if not gid or not games:
            continue
        sums = g[cols].sum()
        out[str(gid)] = {c: float(sums[c]) / games for c in cols}
    return out


NO_MARKET = "no market for him in this week's snapshots"


class MarketWeek:
    """One week's derived file, indexed by gsis id for the board."""

    def __init__(self, blob: Mapping[str, object]) -> None:
        self.season = int(blob["season"])
        self.week = int(blob["week"])
        seen: dict[str, tuple[Mapping, Mapping]] = {}
        twice: set[str] = set()
        games = blob.get("games") or {}
        for gid_game, game in games.items():
            if not isinstance(game, Mapping):
                continue
            for gid, entry in (game.get("players") or {}).items():
                if gid in seen:
                    twice.add(gid)
                seen[gid] = (game, entry)
        self._players = {g: v for g, v in seen.items() if g not in twice}
        self.duplicates = frozenset(twice)
        self.games = len(games)

    def __len__(self) -> int:
        return len(self._players)

    def project(self, gsis_id: str, position: str, kickoff: datetime | None,
                rates: Mapping[str, float] | None, *, rules: ScoringRules = DEFAULT_SCORING,
                kicking: Mapping[str, float] = KICKING_SCORING) -> MarketProjection:
        if gsis_id in self.duplicates:
            return MarketProjection(None, "in two games' snapshots this week")
        hit = self._players.get(gsis_id)
        if hit is None:
            return MarketProjection(None, NO_MARKET)
        game, entry = hit
        taken = _ts(game.get("taken_at"))
        if kickoff is None or taken is None:
            return MarketProjection(None, "kickoff or snapshot time unknown")
        if not taken < kickoff:
            return MarketProjection(None, "snapshot not taken before his kickoff")
        res = market_projection(entry, position, rates, rules=rules, kicking=kicking)
        return MarketProjection(res.points, res.reason, res.market_stats, res.rate_stats,
                                res.books, str(entry.get("basis") or ""),
                                str(game.get("taken_at") or ""))


def market_projection(entry: Mapping[str, object], position: str,
                      rates: Mapping[str, float] | None, *,
                      rules: ScoringRules = DEFAULT_SCORING,
                      kicking: Mapping[str, float] = KICKING_SCORING) -> MarketProjection:
    """Expected league points from one player's derived market entry.

    `rates` is his per-game rate this season (`season_rates`), or None when
    he has no game yet: then the stats no market covers are unknown and the
    projection abstains rather than read them as zero."""
    pos = str(position or "").upper()
    stats = dict(entry.get("stats") or {})
    books = dict(entry.get("books") or {})
    core = POSITION_CORE.get(pos)
    if core is None:
        return MarketProjection(None, f"no market projection for position {pos or '?'}")
    unread = sorted(s for s in (entry.get("abstained") or {}) if s in _SCORED_MARKET_STATS)
    if unread:
        # The market priced a scored stat and it could not be read: filling
        # it from his own rate would pass a hybrid off as the market's view.
        return MarketProjection(None, f"a priced market could not be read ({', '.join(unread)})")
    if not any(s in stats for s in core):
        return MarketProjection(None, f"position-core market missing ({' or '.join(core)})")
    if rates is None:
        return MarketProjection(None, "no game this season: the per-game rates for the "
                                      "stats no market covers are unknown")
    n_books = min((int(books[s]) for s in core if s in books), default=0)
    if pos == "K":
        return _kicker(stats, rates, kicking, n_books)

    line = {c: float(rates.get(c, 0.0)) for c in rates}
    used: list[str] = []
    for s in _MARKET_STATS:
        if s in stats:
            line[s] = float(stats[s])
            used.append(s)
    if "scrimmage_tds" in stats:
        lam = float(stats["scrimmage_tds"])
        ry = max(float(line.get("rushing_yards", 0.0)), 0.0)
        cy = max(float(line.get("receiving_yards", 0.0)), 0.0)
        # Split by expected yards: the league scores both 6 today, so the
        # split moves no point; it matters only if the weights ever differ.
        share = ry / (ry + cy) if ry + cy > 0 else 0.5
        line["rushing_tds"], line["receiving_tds"] = lam * share, lam * (1.0 - share)
        used.extend(("rushing_tds", "receiving_tds"))
    from_rates = tuple(sorted(c for c, v in rates.items()
                              if c not in used and v and not c.startswith(("fg_", "pat_"))))
    return MarketProjection(fantasy_points(line, rules), "", tuple(used), from_rates, n_books)


def _kicker(stats: Mapping[str, object], rates: Mapping[str, float],
            kicking: Mapping[str, float], n_books: int) -> MarketProjection:
    """The market's kicking points split into league points by his own
    season: makes scaled so their book points equal the market's
    expectation, distance mix and misses at his per-game rates."""
    made = {c: float(rates.get(c, 0.0)) for c in (*_FG_MADE, "pat_made")}
    book = (BOOK_KICKING_POINTS["fg"] * sum(made[c] for c in _FG_MADE)
            + BOOK_KICKING_POINTS["pat"] * made["pat_made"])
    if book <= 0:
        return MarketProjection(None, "no field goal or extra point made this season to "
                                      "split the market's kicking points")
    scale = float(stats["kicking_points"]) / book
    line = {c: v * scale for c, v in made.items()}
    line["fg_missed"] = float(rates.get("fg_missed", 0.0))
    line["pat_missed"] = float(rates.get("pat_missed", 0.0))
    return MarketProjection(kicker_points(line, kicking), "", ("kicking_points",),
                            ("distance mix", "fg_missed", "pat_missed"), n_books)


def market_block(week: MarketWeek | None, players: Sequence[object],
                 rates: Mapping[str, Mapping[str, float]], *,
                 rules: ScoringRules = DEFAULT_SCORING) -> tuple[dict, dict]:
    """(sleeper_id -> points, detail) for the board's `market_v1` contender.
    `players` are board Players (sleeper_id, gsis_id, position, kickoff):
    their sleeper->gsis resolution already happened upstream through
    `gridiron.ids.Crosswalk` (rule #3); nothing here maps one id onto the
    other, and the market entry is found by gsis id alone."""
    numbers: dict[str, float] = {}
    rate_notes: dict[str, list[str]] = {}
    why: Counter = Counter()
    basis: Counter = Counter()
    no_market = 0
    if week is None:
        return numbers, {}
    for p in players:
        gid = str(getattr(p, "gsis_id", "") or "")
        if not gid:
            continue
        res = week.project(gid, getattr(p, "position", ""), getattr(p, "kickoff", None),
                           rates.get(gid), rules=rules)
        if res.points is None:
            if res.reason == NO_MARKET:
                no_market += 1
            else:
                why[res.reason] += 1
            continue
        sid = str(getattr(p, "sleeper_id"))
        numbers[sid] = round(float(res.points), 3)
        basis[res.basis or "?"] += 1
        if res.rate_stats:
            rate_notes[sid] = list(res.rate_stats)
    detail = {"season": week.season, "week": week.week, "snapshot_games": week.games,
              "projected": len(numbers), "no_market": no_market,
              "abstained": dict(sorted(why.items())),
              "identity_basis": dict(sorted(basis.items())),
              "from_rates": rate_notes,
              "note": ("market_v1: expected stats from player props (consensus of books, "
                       "de-vigged), scored by gridiron.scoring; stats no market covers come "
                       "from his own per-game rate this season (listed in from_rates). "
                       "Grading only; identity rows may be machine 'exact' (record-only)")}
    return numbers, detail
