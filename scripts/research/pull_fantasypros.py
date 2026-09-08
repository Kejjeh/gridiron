"""Fetch FantasyPros half-PPR ADP + draft projections (public pages). Output -> data/research/cache/draft2026/."""
import pathlib, re, json, time, io
import requests, pandas as pd
OUT = pathlib.Path("data/research/cache/draft2026"); OUT.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gridiron-research/0.1", "Accept": "text/html"}
def fetch(name, url):
    r = requests.get(url, headers=H, timeout=60)
    print(name, r.status_code, len(r.text))
    (OUT / f"{name}.html").write_text(r.text, encoding="utf-8")
    return r.text
def tables(name, html):
    try:
        ts = pd.read_html(io.StringIO(html))
        for i, t in enumerate(ts):
            t.to_csv(OUT / f"{name}_t{i}.csv", index=False)
            print(f"  table {i}: {t.shape} {list(t.columns)[:8]}")
    except Exception as e:
        print("  read_html failed:", e)
html = fetch("fp_adp_half", "https://www.fantasypros.com/nfl/adp/half-point-ppr-overall.php"); tables("fp_adp_half", html)
for pos in ["qb", "rb", "wr", "te", "k", "dst"]:
    html = fetch(f"fp_proj_{pos}", f"https://www.fantasypros.com/nfl/projections/{pos}.php?week=draft&scoring=HALF"); tables(f"fp_proj_{pos}", html)
    time.sleep(1)
html = fetch("fp_ecr_half", "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php")
m = re.search(r"var ecrData = (\{.*?\});", html, re.S)
if m:
    data = json.loads(m.group(1)); (OUT / "fp_ecr_half.json").write_text(json.dumps(data))
    print("ecrData players:", len(data.get("players", [])), "last_updated", data.get("last_updated"))
else:
    print("no ecrData found")
