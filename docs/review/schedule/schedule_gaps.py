"""Scheduled-build gaps vs the cron in effect on main (evidence, 2026-09-25).

Offline and deterministic: the run creation times below were read from the
GitHub Actions API (every run of both workflows, none omitted) and are
typed in, so re-running prints the same table. Nothing here calls GitHub.
See schedule-gaps-2026-09-25.txt for method, sources and conclusion.
"""
from datetime import datetime, timedelta, timezone
def t(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
NOW = t("2026-09-25T08:05:00Z")
dash = [(4,"2026-09-21T07:03:53Z"),(5,"2026-09-21T14:30:54Z"),(6,"2026-09-21T19:42:34Z"),(7,"2026-09-21T23:33:02Z"),
(8,"2026-09-22T01:48:37Z"),(9,"2026-09-22T07:55:55Z"),(10,"2026-09-22T13:38:28Z"),(11,"2026-09-22T17:44:56Z"),
(12,"2026-09-22T21:38:00Z"),(13,"2026-09-22T23:47:13Z"),(14,"2026-09-23T05:06:46Z"),(15,"2026-09-23T10:15:15Z"),
(16,"2026-09-23T15:10:31Z"),(17,"2026-09-23T19:11:50Z"),(18,"2026-09-23T22:26:16Z"),(19,"2026-09-24T00:50:55Z"),
(20,"2026-09-24T06:42:02Z"),(21,"2026-09-24T12:52:25Z"),(22,"2026-09-24T17:56:08Z"),(23,"2026-09-24T23:53:47Z"),
(24,"2026-09-25T04:43:43Z")]
sync = [(n,s) for n,s in [(2,"2026-09-19T23:09:39Z"),(3,"2026-09-20T01:12:36Z"),(4,"2026-09-20T06:21:24Z"),(5,"2026-09-20T12:25:18Z"),
(6,"2026-09-20T16:50:06Z"),(7,"2026-09-20T19:28:56Z"),(8,"2026-09-20T22:28:50Z"),(9,"2026-09-21T01:09:23Z"),
(11,"2026-09-21T06:24:27Z"),(12,"2026-09-21T14:13:49Z"),(13,"2026-09-21T19:32:22Z"),(14,"2026-09-21T23:19:06Z"),
(15,"2026-09-22T05:01:18Z"),(16,"2026-09-22T10:00:15Z"),(17,"2026-09-22T14:57:30Z"),(18,"2026-09-22T18:56:54Z"),
(19,"2026-09-22T22:12:45Z"),(20,"2026-09-23T00:38:05Z"),(21,"2026-09-23T06:01:03Z"),(22,"2026-09-23T11:42:28Z"),
(23,"2026-09-23T17:05:46Z"),(24,"2026-09-23T20:25:40Z"),(25,"2026-09-23T23:44:19Z"),(26,"2026-09-24T04:56:18Z"),
(27,"2026-09-24T10:04:38Z"),(28,"2026-09-24T15:09:40Z"),(29,"2026-09-24T19:12:59Z"),(30,"2026-09-24T22:27:44Z"),
(31,"2026-09-25T01:33:15Z"),(32,"2026-09-25T07:52:48Z")]]
SWITCH = t("2026-09-24T21:15:46Z")   # 63071af merged: dashboard cron :41 -> 7,22,37,52
def dash_mins(at): return (41,) if at < SWITCH else (7,22,37,52)
def slots(a, b, mins):   # cron slots in (a, b]
    n, x = 0, a.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while x <= b:
        n += x.minute in mins(x) if callable(mins) else x.minute in mins
        x += timedelta(minutes=1)
    return n
def prev_slot(at, mins):
    x = at.replace(second=0, microsecond=0)
    while x.minute not in (mins(x) if callable(mins) else mins): x -= timedelta(minutes=1)
    return x
def report(name, runs, mins, start):
    print(f"\n{name}")
    print("run  created (UTC)      cron-in-effect   gap-from-prev  slots-in-gap  runs  min-lateness")
    prev = start; gaps=[]; late=[]
    for n,s in runs:
        at = t(s); m = mins(at) if callable(mins) else mins
        g = at - prev; k = slots(prev, at, mins); ps = prev_slot(at, mins)
        gaps.append(g); late.append((at-ps).total_seconds()/60)
        print(f"#{n:<3} {s[5:16].replace('T',' ')}  {','.join(map(str,m)):<15}  {str(g)[:-3]:>9}      {k:>4}        1     {late[-1]:5.1f} min")
        prev = at
    total = slots(start, runs[-1][0] and t(runs[-1][1]), mins)
    print(f"  median gap {sorted(gaps)[len(gaps)//2]}, max {max(gaps)}, min {min(gaps)}; lateness vs nearest earlier slot: min {min(late):.0f}, max {max(late):.0f} min")
    tail = NOW - t(runs[-1][1]); print(f"  since last run to {NOW:%H:%MZ}: {tail} with {slots(t(runs[-1][1]), NOW, mins)} slots, no run")
report("DASHBOARD (dashboard-artifact.yml), scheduled runs only", dash, dash_mins, t("2026-09-21T01:25:12Z"))
report("SYNC (sleeper-sync.yml, cron 17 * * * * throughout)", sync, (17,), t("2026-09-19T20:44:01Z"))
d_old = [t(s) for n,s in dash if t(s) < SWITCH]; s_all=[t(s) for n,s in sync]
print("\nDashboard, :41 era  (01:25Z Sep21 -> 21:15Z Sep24):", len(d_old), "runs for", slots(t("2026-09-21T01:25:12Z"), SWITCH, (41,)), "slots")
print("Dashboard, 15-min era (21:15Z Sep24 -> 08:05Z Sep25):", 2, "runs for", slots(SWITCH, NOW, (7,22,37,52)), "slots")
print("Sync overall:", len(sync), "runs for", slots(t("2026-09-19T20:44:01Z"), NOW, (17,)), "slots")

# What-if (NOT a guarantee): the dashboard also builds after each completed sync run.
lo = t("2026-09-21T06:00:00Z")
union = sorted([t(s) for n,s in dash if t(s) >= lo] + [t(s) + timedelta(minutes=1) for n,s in sync if t(s) >= lo])
own = sorted(t(s) for n,s in dash if t(s) >= lo)
def stats(xs):
    g = sorted((b-a) for a,b in zip(xs, xs[1:])); h = lambda d: f"{d.total_seconds()/3600:.1f}h"
    return f"{len(xs)} builds, gaps median {h(g[len(g)//2])}, max {h(g[-1])}, p90 {h(g[int(len(g)*.9)])}"
print("\nWhat-if over the same window (Sep21 06:00Z -> Sep25 08:05Z):")
print("  dashboard's own schedule events:        ", stats(own))
print("  plus a build after each sync completion:", stats(union))
