"""Run offline exploratory tests. PYTHONPATH=src python scripts/research/test_draft_theories.py"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from gridiron.paths import RESEARCH_CACHE, OUTPUTS, DOCS, REPO_ROOT
from gridiron.draft_theories import (POSITIONS, REPLACEMENT, LIMITS, score_rows, stable_crosswalk,
    preseason_snapshot, attach_outcomes, evaluate_distributions, team_games, schedule_features,
    evaluate_sos, block_interval)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Download missing raw caches; preserve existing snapshots')
    args=parser.parse_args()
    cache=RESEARCH_CACHE/'draft_theories'
    if args.fetch:
        import nflreadpy as nfl
        cache.mkdir(parents=True, exist_ok=True)
        jobs=[(cache/'rankings_all.parquet', lambda:nfl.load_ff_rankings('all')),
              (cache/'schedules.parquet', lambda:nfl.load_schedules(list(range(2019,2026))))]
        jobs += [(cache/f'stats_{y}.parquet', lambda y=y:nfl.load_player_stats([y])) for y in range(2019,2026)]
        for target, loader in jobs:
            if not target.exists(): loader().write_parquet(target)
        ids=RESEARCH_CACHE/'draft2026'/'ff_playerids.csv'
        if not ids.exists():
            ids.parent.mkdir(parents=True,exist_ok=True)
            nfl.load_ff_playerids().write_csv(ids)
    source_files=[cache/'rankings_all.parquet',cache/'schedules.parquet',
        RESEARCH_CACHE/'draft2026'/'ff_playerids.csv']+[cache/f'stats_{y}.parquet' for y in range(2019,2026)]
    hashes={str(p.relative_to(RESEARCH_CACHE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    rankings=pd.read_parquet(cache/'rankings_all.parquet')
    schedules=pd.read_parquet(cache/'schedules.parquet')
    crosswalk,ambiguous=stable_crosswalk(pd.read_csv(source_files[2]))
    stats=pd.concat([pd.read_parquet(cache/f'stats_{y}.parquet') for y in range(2019,2026)],ignore_index=True)
    expected_games=set(schedules.loc[schedules.game_type.eq('REG') & schedules.home_score.notna() & schedules.away_score.notna(),'game_id'])
    observed_games=set(stats.loc[stats.season_type.eq('REG'),'game_id'])
    if expected_games - observed_games:
        raise ValueError('Incomplete outcome feed: completed games are missing')
    # Target is W1-W17 individual production, including missed weeks; no hindsight substitutes.
    scoring=stats[stats.season_type.eq('REG') & stats.week.between(1,17)].copy()
    if scoring.duplicated(['season','week','player_id']).any(): raise ValueError('Duplicate player weeks')
    scoring['points']=score_rows(scoring)
    coverage=[]; cohorts=[]
    for year in range(2020,2026):
        kickoff=pd.to_datetime(schedules[(schedules.season==year)&schedules.game_type.eq('REG')].gameday).min()
        snap=preseason_snapshot(rankings,year,kickoff)
        totals=scoring[scoring.season==year].groupby('player_id').points.sum()
        d=attach_outcomes(snap,totals,crosswalk)
        for pos,g in d.groupby('pos'):
            coverage.append(dict(season=year,pos=pos,snapshot=str(g.date.max().date()),ranked=len(g),
                matched=int(g.gsis_id.notna().sum()),zero_outcome=int((g.actual==0).sum()),
                unmatched_ids=[int(x) for x in g.loc[g.gsis_id.isna(),'id']]))
        cohorts.append(d[d.gsis_id.notna()])
    cohort=pd.concat(cohorts,ignore_index=True)
    forecasts=pd.concat([evaluate_distributions(cohort[cohort.season<y],cohort[cohort.season==y])
                         for y in (2023,2024,2025)],ignore_index=True)
    completed=schedules[schedules.home_score.notna()&schedules.away_score.notna()]
    sos=evaluate_sos(schedule_features(team_games(stats,completed)))
    forecasts.to_csv(OUTPUTS/'draft_theories_predictions.csv',index=False)
    sos.to_csv(OUTPUTS/'draft_theories_sos_predictions.csv',index=False)
    pd.DataFrame(coverage).to_csv(OUTPUTS/'draft_theories_coverage.csv',index=False)
    summaries=[]
    for metric in ('pinball','upside_brier'):
        yearly=forecasts.groupby(['season','model'])[metric].mean().unstack()
        for model in ('position','position_rank'):
            delta=yearly[model]-yearly.pooled
            summaries.append(dict(metric=metric,model=model,baseline=float(yearly.pooled.mean()),
                candidate=float(yearly[model].mean()),delta=float(delta.mean()),
                interval=block_interval(delta),improved_seasons=int((delta<0).sum()),
                by_season={int(k):float(v) for k,v in delta.items()}))
    sos_summary=[]
    for (pos,window),g in sos.groupby(['pos','window']):
        yearly=g.groupby(['season','model']).squared_error.mean().unstack()
        delta=yearly.plus_sos-yearly.baseline
        sos_summary.append(dict(pos=pos,window=window,baseline_rmse=float(np.sqrt(yearly.baseline.mean())),
            candidate_rmse=float(np.sqrt(yearly.plus_sos.mean())),delta_mse=float(delta.mean()),
            interval=block_interval(delta),improved_seasons=int((delta<0).sum())))
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),seed=20260908,
        source_hashes=hashes,
        code_hashes={name:hashlib.sha256((REPO_ROOT/name).read_bytes()).hexdigest() for name in
            ['src/gridiron/draft_theories.py','scripts/research/test_draft_theories.py','src/gridiron/scoring.py','src/gridiron/league_config.py']},
        package_versions={name:importlib.metadata.version(name) for name in ['numpy','pandas','polars','nflreadpy']},
        parameters={'quantiles':'0.05 to 0.95 step 0.05','subgroup_pooled_prior':20,'rank_bands':3,
                    'upside_margin_points':25,'bootstrap_season_draws':5000},coverage=coverage,ambiguous_crosswalk_ids=len(ambiguous),
        test_seasons=[2023,2024,2025],rank_limits=LIMITS,replacement_ranks=REPLACEMENT,
        distribution_results=summaries,sos_results=sos_summary,production_approved=False,
        scope='Partial rank-based test, NOT replication using archived preseason point projections',
        sources={'rankings':'https://github.com/DynastyProcess/data',
                 'outcomes':'https://nflreadr.nflverse.com/reference/load_player_stats.html',
                 'schedule':'https://github.com/nflverse/nfldata',
                 'crosswalk':'https://github.com/dynastyprocess/data/blob/master/files/db_playerids.csv'})
    (OUTPUTS/'draft_theories_manifest.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    distribution_gain=next(r for r in summaries if r['metric']=='pinball' and r['model']=='position_rank')
    breakout_gain=next(r for r in summaries if r['metric']=='upside_brier' and r['model']=='position_rank')
    wr_middle=next(r for r in sos_summary if r['pos']=='WR' and r['window']=='middle')
    lines=['# Historical draft-theory tests','',
        'Readiness: no new numerical weights approved for the live draft model. These are exploratory partial tests.', '',
        '## Findings', '',
        f"Conditioning uncertainty on position and draft-rank thirds changed mean quantile loss by {100*distribution_gain['delta']/distribution_gain['baseline']:+.2f}% "
        f"and breakout Brier loss by {100*breakout_gain['delta']/breakout_gain['baseline']:+.2f}% versus pooled uncertainty. "
        'This supports further work on outcome ranges; it does not validate numerical point-projection adjustments.', '',
        'Simple historical SOS did not consistently help early WR or playoff RB forecasts. '
        f"WR weeks 5-14 RMSE changed {100*(wr_middle['candidate_rmse']/wr_middle['baseline_rmse']-1):+.2f}%, among 12 examined position/window cells. "
        'No blanket RB discount or live SOS weight is approved.', '',
        '## What was actually tested','',
        'Six dated preseason PPR ranking cohorts (2020-2025), scored using the verified league half-PPR formula. '
        'Point projections archived before those seasons were not obtained. Ranking-derived expectations are a proxy '
        'and do not replicate the linked projection-error studies. Current player names/teams/ages are not features; '
        'the current crosswalk is used only for stable identity. Unknown IDs are excluded and logged. Ranked players '
        'with resolved identity and no recorded regular-season production remain zero outcomes.', '',
        'Training expands chronologically: 2020-2022 -> 2023, 2020-2023 -> 2024, 2020-2024 -> 2025. '
        'Within each training block, leave-one-season-out residuals calibrate distributions; none use the target season. '
        'The mean baseline fits points to log positional rank separately for QB/RB/WR/TE. '
        'Residual candidates pool all positions, separate positions, or separate position and fixed rank thirds. '
        'Each subgroup receives 20 fixed quantiles from the pooled residual distribution as shrinkage. '
        'All choices were fixed before viewing test scores; no tuning on held-out outcomes.', '',
        'Outcome is individual W1-W17 core offensive production, including missed weeks. Return/recovery touchdowns and other special-teams scoring are outside this canonical offensive formula. No optimal hindsight replacements, '
        'weekly lineup decisions, waiver competition or championship-probability claims. '
        'The ranking archive uses PPR, while outcomes use half-PPR; this limits transferability.', '',
        '## Outcome distribution and upside results','',
        'Lower loss is better. Pinball is the average quantile loss at 5%, 10%, ..., 95%. '
        'Upside Brier scores the probability of exceeding max(baseline expected points, estimated replacement points) '
        'by more than 25 points. Replacement ranks are QB16/RB40/WR48/TE20, not optimized league waiver levels.', '',
        '| Test | Candidate | Baseline | Candidate loss | Change | Season-block 95% interval | Better seasons |',
        '|---|---|---:|---:|---:|---|---:|']
    for r in summaries:
        lines.append(f"| {r['metric']} | {r['model']} | {r['baseline']:.4f} | {r['candidate']:.4f} | {r['delta']:+.4f} | [{r['interval'][0]:+.4f}, {r['interval'][1]:+.4f}] | {r['improved_seasons']}/3 |")
    lines+=['','| Model | 80% interval coverage | Mean interval width (points) |','|---|---:|---:|']
    for model,g in forecasts.groupby('model'):
        lines.append(f'| {model} | {g.coverage80.mean():.1%} | {g.width80.mean():.1f} |')
    lines+=['','## Schedule test','',
        'This tests prior-year fantasy points allowed, NOT Subvertadown proprietary SOS, ESPN line win rates, '
        'PFF grades or personnel adjustments. The baseline is a fitted prior-year team-position points-per-game model. '
        'The added feature is the average prior-year opponent points allowed minus the league position mean. '
        'Both models fit only earlier seasons, separately by position and window. '
        'Every completed team game is represented, including zero-production positions. Canceled games are excluded. '
        'Final historical schedules are used for opponent/week assignment, so rescheduling revisions are a limitation; '
        'scores only identify completed games and never enter a predictor.', '',
        '| Position | Window | Baseline RMSE | With SOS RMSE | MSE change | Season-block 95% interval | Better seasons |',
        '|---|---|---:|---:|---:|---|---:|']
    for r in sos_summary:
        lines.append(f"| {r['pos']} | {r['window']} | {r['baseline_rmse']:.3f} | {r['candidate_rmse']:.3f} | {r['delta_mse']:+.3f} | [{r['interval'][0]:+.3f}, {r['interval'][1]:+.3f}] | {r['improved_seasons']}/3 |")
    lines+=['','## Coverage','', '| Season | Position | Snapshot | Ranked | ID matched | Zero outcomes |','|---|---|---|---:|---:|---:|']
    for r in coverage:
        lines.append(f"| {r['season']} | {r['pos']} | {r['snapshot']} | {r['ranked']} | {r['matched']} | {r['zero_outcome']} |")
    lines+=['','## Interpretation limits and promotion gate','',
        '- Three held-out season clusters are too few for a strong generalization claim. Bootstrap intervals are descriptive; '
        'they do not create more independent seasons. Multiple candidate/window comparisons were not corrected.',
        '- The hypotheses were inspired by articles that already considered seasons through 2025. These years are held out '
        'from our fitting, but not a pristine independent confirmation of the published theories.',
        '- A low-risk position is not necessarily the best draft pick. Replacement supply, roster needs, price, '
        'within-team correlations and weekly lineup decisions remain untested.',
        '- No calibrated mean discount, added upside bonus, roster win probability, or production feature is justified by this run.',
        '- Required next data: preseason point/stat projections with immutable dates, stable IDs, scoring definitions, '
        'and coverage of players who later missed the season; historical SOS forecasts and archived schedule releases.',
        '- Then compare against ALL live-model features, use additional unseen seasons, validate weekly roster decisions '
        'and evaluate replacement assumptions before any live promotion.', '',
        '## Data provenance and reproducibility','',
        'The JSON manifest records SHA-256 hashes of every local input, research/scoring code, library versions, parameters and fixed seed. '
        'Raw downloads are cached and excluded from git. Historical statistics were retrieved now and may include corrections; '
        'ranking scrape dates come from the archive and were screened before each kickoff. Independent pre-kickoff commit '
        'attestation for each ranking snapshot was not established.', '',
        '[Ranking archive](https://github.com/DynastyProcess/data) · '
        '[NFL data](https://github.com/nflverse/nflverse-data) · '
        '[Schedule archive](https://github.com/nflverse/nfldata)', '',
        '```powershell','$env:PYTHONPATH="src"',
        '# Optional, only if raw caches are absent:',
        '# .venv/Scripts/python.exe scripts/research/test_draft_theories.py --fetch',
        '.venv/Scripts/python.exe scripts/research/test_draft_theories.py',
        '.venv/Scripts/python.exe scripts/ci/smoke.py',
        '.venv/Scripts/python.exe scripts/ci/run_summary.py -- .venv/Scripts/python.exe -m pytest','```','']
    (DOCS/'research'/'DRAFT_THEORIES_RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'distribution':summaries,'sos':sos_summary},indent=2))


if __name__=='__main__': main()
