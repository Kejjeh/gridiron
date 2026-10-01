"""Exploratory historical tests; no production ranking weights are exported."""
from __future__ import annotations

import numpy as np
import pandas as pd
from gridiron.scoring import fantasy_points

POSITIONS = ('QB', 'RB', 'WR', 'TE')
LIMITS = {'QB': 24, 'RB': 72, 'WR': 96, 'TE': 36}
REPLACEMENT = {'QB': 16, 'RB': 40, 'WR': 48, 'TE': 20}
QUANTILES = np.arange(.05, 1, .05)


def score_rows(frame):
    """Normalize nflverse schema, then call the repository's only scoring formula."""
    d = frame.copy()
    d['interceptions'] = d['passing_interceptions']
    d['fumbles_lost'] = d[['sack_fumbles_lost', 'rushing_fumbles_lost', 'receiving_fumbles_lost']].fillna(0).sum(axis=1)
    d['two_point_conversions'] = d[['passing_2pt_conversions', 'rushing_2pt_conversions', 'receiving_2pt_conversions']].fillna(0).sum(axis=1)
    return np.array([fantasy_points(r) for r in d.fillna(0).to_dict('records')])


def stable_crosswalk(frame):
    d = frame[['fantasypros_id', 'gsis_id']].dropna().copy()
    d['id'] = pd.to_numeric(d.fantasypros_id).astype(int)
    counts = d.groupby('id').gsis_id.nunique()
    ambiguous = set(counts[counts != 1].index)
    return d[~d.id.isin(ambiguous)].drop_duplicates('id').set_index('id').gsis_id.to_dict(), ambiguous


def preseason_snapshot(rankings, season, kickoff):
    d = rankings.copy()
    d['date'] = pd.to_datetime(d.scrape_date)
    kickoff = pd.Timestamp(kickoff)
    d = d[d.fp_page.isin(['ppr-cheatsheets', '/nfl/rankings/ppr-cheatsheets.php'])
          & (d.date < kickoff) & (d.date >= kickoff - pd.Timedelta(days=30))]
    if d.empty:
        raise ValueError(f'No preseason ranking snapshot for {season}')
    d = d[d.date == d.date.max()].copy()
    d['id'] = pd.to_numeric(d.id, errors='raise').astype(int)
    if d.id.duplicated().any():
        raise ValueError('Duplicate ranking IDs within snapshot')
    d = d[d.pos.isin(POSITIONS)].sort_values(['ecr', 'id'])
    d['rank'] = d.groupby('pos').cumcount() + 1
    d['season'] = season
    return d[d['rank'] <= d.pos.map(LIMITS)].copy()


def attach_outcomes(snapshot, totals, crosswalk):
    """Keep ranked nonparticipants as zero; unknown identities remain explicitly missing."""
    d = snapshot.copy()
    d['gsis_id'] = d.id.map(crosswalk)
    if d.gsis_id.dropna().duplicated().any():
        raise ValueError('Multiple ranking IDs map to one GSIS ID')
    d['actual'] = d.gsis_id.map(totals)
    d.loc[d.gsis_id.notna(), 'actual'] = d.loc[d.gsis_id.notna(), 'actual'].fillna(0)
    return d


def check_split(train, test):
    if train.empty or test.empty or train.season.max() >= test.season.min():
        raise ValueError('Training seasons must strictly precede test seasons')


def design(rank):
    return np.column_stack([np.ones(len(rank)), np.log(np.asarray(rank, dtype=float))])


def mean_predictions(train, test):
    result = np.zeros(len(test))
    for pos in POSITIONS:
        a = train[train.pos == pos]
        mask = (test.pos == pos).to_numpy()
        if len(a) < 20:
            raise ValueError('Insufficient training support')
        beta = np.linalg.lstsq(design(a['rank']), a.actual.to_numpy(), rcond=None)[0]
        result[mask] = np.maximum(0, design(test.loc[mask, 'rank']) @ beta)
    return result


def crossfit_residuals(train):
    """Leave-one-training-season-out residuals; every included season predates test."""
    pieces = []
    for season in sorted(train.season.unique()):
        a, b = train[train.season != season], train[train.season == season].copy()
        b['mean'] = mean_predictions(a, b)
        b['residual'] = b.actual - b['mean']
        pieces.append(b)
    return pd.concat(pieces, ignore_index=True)


def rank_band(rank, pos):
    return min(2, int((rank - 1) * 3 / LIMITS[pos]))


def distribution_samples(residuals, row, model):
    # A fixed 20-observation pooled prior prevents tiny groups dominating.
    pool = residuals.residual.to_numpy()
    local = residuals
    if model in ('position', 'position_rank'):
        local = local[local.pos == row.pos]
    if model == 'position_rank':
        band = rank_band(row['rank'], row.pos)
        local = local[local.apply(lambda r: rank_band(r['rank'], r.pos), axis=1) == band]
    values = local.residual.to_numpy()
    if model == 'pooled':
        return pool
    prior = np.quantile(pool, (np.arange(20) + .5) / 20)
    return np.concatenate([values, prior])


def evaluate_distributions(train, test):
    check_split(train, test)
    residuals = crossfit_residuals(train)
    b = test.copy().reset_index(drop=True)
    b['mean'] = mean_predictions(train, b)
    reference = pd.DataFrame([{'pos': p, 'rank': r} for p, r in REPLACEMENT.items()])
    replacements = dict(zip(reference.pos, mean_predictions(train, reference)))
    rows = []
    for _, r in b.iterrows():
        threshold = max(r['mean'], replacements[r.pos])
        for model in ('pooled', 'position', 'position_rank'):
            samples = np.maximum(0, r['mean'] + distribution_samples(residuals, r, model))
            q = np.quantile(samples, QUANTILES)
            err = r.actual - q
            loss = float(np.maximum(QUANTILES * err, (QUANTILES - 1) * err).mean())
            lo, hi = np.quantile(samples, [.1, .9])
            probability = float((samples > threshold + 25).mean())
            upside = max(0, r.actual - threshold)
            rows.append(dict(season=int(r.season), id=r.gsis_id, pos=r.pos, rank=int(r['rank']), model=model,
                actual=float(r.actual), expected=float(r['mean']), pinball=loss,
                coverage80=float(lo <= r.actual <= hi), width80=float(hi-lo),
                upside_actual=upside, upside_predicted=float(np.maximum(0, samples-threshold).mean()),
                upside_brier=(probability-float(upside>25))**2,
                forecast_error=float(r.actual-r['mean'])))
    return pd.DataFrame(rows)


def block_interval(deltas, seed=20260908):
    """Equal-weight season-cluster bootstrap; few clusters are disclosed, not hidden."""
    values = np.asarray(deltas, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(values, (5000, len(values)), replace=True).mean(axis=1)
    return [float(x) for x in np.quantile(means, [.025, .975])]


def normalize_team(value):
    return {'LAR': 'LA', 'STL': 'LA', 'OAK': 'LV', 'SD': 'LAC', 'JAC': 'JAX'}.get(value, value)


def team_games(stats, schedules):
    """Complete team-game-position grid: no recorded stats is zero, not missing game."""
    s = schedules[schedules.game_type.eq('REG')].copy()
    homes = s[['season', 'week', 'game_id', 'home_team', 'away_team']].rename(columns={'home_team':'team','away_team':'opponent'})
    away = s[['season', 'week', 'game_id', 'away_team', 'home_team']].rename(columns={'away_team':'team','home_team':'opponent'})
    grid = pd.concat([homes, away]).merge(pd.DataFrame({'pos':['RB','WR','TE']}), how='cross')
    for c in ['team','opponent']: grid[c] = grid[c].map(normalize_team)
    a = stats[stats.season_type.eq('REG') & stats.position.isin(['RB','WR','TE'])].copy()
    a['team'] = a.team.map(normalize_team)
    a['points'] = score_rows(a)
    a = a.groupby(['season','week','team','position']).points.sum().reset_index().rename(columns={'position':'pos'})
    d = grid.merge(a, on=['season','week','team','pos'], how='left', validate='one_to_one')
    d['points'] = d.points.fillna(0)
    return d


def schedule_features(games):
    rows = []
    windows = {'early':(1,4), 'middle':(5,14), 'playoffs':(15,17), 'all':(1,17)}
    for season in range(2020,2026):
        past = games[games.season == season-1]
        current = games[games.season == season]
        for pos in ('RB','WR','TE'):
            p = past[past.pos==pos]
            offense = p.groupby('team').points.mean()
            defense = p.groupby('opponent').points.mean()
            for window,(lo,hi) in windows.items():
                c = current[(current.pos==pos)&current.week.between(lo,hi)].copy()
                c['sos'] = c.opponent.map(defense) - p.points.mean()
                c['baseline'] = c.team.map(offense)
                if c[['sos','baseline']].isna().any().any(): raise ValueError('Missing historical team support')
                for team,g in c.groupby('team'):
                    rows.append(dict(season=season,pos=pos,window=window,team=team,
                        baseline=float(g.baseline.mean()),sos=float(g.sos.mean()),actual=float(g.points.mean())))
    return pd.DataFrame(rows)


def evaluate_sos(frame):
    rows=[]
    for year in (2023,2024,2025):
        for pos in ('RB','WR','TE'):
            for window in ('early','middle','playoffs','all'):
                group=frame[(frame.pos==pos)&(frame.window==window)]
                a,b=group[group.season<year],group[group.season==year]
                check_split(a,b)
                for name,columns in [('baseline',['baseline']),('plus_sos',['baseline','sos'])]:
                    x=np.column_stack([np.ones(len(a)),a[columns].to_numpy()])
                    xt=np.column_stack([np.ones(len(b)),b[columns].to_numpy()])
                    beta=np.linalg.lstsq(x,a.actual.to_numpy(),rcond=None)[0]
                    predictions=xt@beta
                    for (_,r),prediction in zip(b.iterrows(),predictions):
                        rows.append(dict(season=year,pos=pos,window=window,team=r.team,model=name,
                            actual=float(r.actual),prediction=float(prediction),
                            squared_error=float((r.actual-prediction)**2),absolute_error=float(abs(r.actual-prediction))))
    return pd.DataFrame(rows)
