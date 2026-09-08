"""Pull the draft-day data set with nflreadpy. Output -> data/research/cache/draft2026/ (gitignored)."""
import pathlib, sys, time
import nflreadpy as nfl
OUT = pathlib.Path("data/research/cache/draft2026"); OUT.mkdir(parents=True, exist_ok=True)
def save(name, fn, *a, **k):
    t = time.time()
    try:
        df = fn(*a, **k)
        df = df.to_pandas() if hasattr(df, "to_pandas") else df
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"{name}: {df.shape} cols={list(df.columns)[:12]}... {time.time()-t:.1f}s", flush=True)
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}: {e}", flush=True)
save("stats_season_2025", nfl.load_player_stats, [2025], summary_level="reg")
save("stats_week_2025", nfl.load_player_stats, [2025], summary_level="week")
save("stats_season_2024", nfl.load_player_stats, [2024], summary_level="reg")
save("rosters_2026", nfl.load_rosters, [2026])
save("depth_charts_2026", nfl.load_depth_charts, [2026])
save("injuries_2026", nfl.load_injuries, [2026])
save("injuries_2025", nfl.load_injuries, [2025])
save("schedules_2026", nfl.load_schedules, [2026])
save("ff_playerids", nfl.load_ff_playerids)
save("ff_rankings_draft", nfl.load_ff_rankings, "draft")
save("ff_rankings_week", nfl.load_ff_rankings, "week")
save("ff_opportunity_2025", nfl.load_ff_opportunity, [2025])
save("snap_counts_2025", nfl.load_snap_counts, [2025])
save("teams", nfl.load_teams)
save("players", nfl.load_players)
save("stats_season_2026", nfl.load_player_stats, [2026], summary_level="reg")
