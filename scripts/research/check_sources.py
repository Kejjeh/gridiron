"""Check sourced research rows mechanically: is the quote on the cited page?

    python -I scripts/research/check_sources.py IN.json OUT.json [--kind roles|hc]

For the play-caller table (workstream B). Every research row carries a
source URL and a verbatim quote. This fetches each URL (stdlib urllib,
honouring the environment's proxy and CA settings), strips the page to text,
and checks that the quote appears on it after normalising whitespace, case,
quote marks and dashes. It also checks that the quote says what the role
claims: an OPC (play-caller) quote must mention calling plays, an OC/DC
quote a coordinator, a head-coach change a firing, an interim or a head
coach. A row passes only when its page was fetched, the quote was found,
and the role words are in the quote.

Rows that fail (a page that will not load or renders by script, a
paraphrased quote) are not dropped silently: OUT lists them, and they go to
a human or agent reader, or stay out of the table. Fetched pages are cached
under data/research/cache/sources/ (gitignored). Run with `python -I`: the
pages are downloaded, untrusted text.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent.parent / "data" / "research" / "cache" / "sources"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 gridiron-research")
ROLE_WORDS = {
    "OPC": ("call", "play-call", "playcall"),
    "OC": ("coordinator", " oc", "oc "),
    "DC": ("coordinator", " dc", "dc "),
    "HC": ("interim", "fired", "head coach", "dismiss", "resign", "relieved"),
}
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_SPACE = re.compile(r"\s+")


def norm(text: str) -> str:
    t = html.unescape(text)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), ("\xa0", " ")):
        t = t.replace(a, b)
    return _SPACE.sub(" ", t).strip().lower()


def bare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text)


def fetch(url: str) -> tuple[str | None, str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".txt")
    if key.exists():
        return key.read_text(encoding="utf-8"), "cached"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=30) as resp:          # noqa: S310
            raw = resp.read(4_000_000).decode("utf-8", errors="replace")
    except Exception as exc:                                           # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    text = norm(_TAGS.sub(" ", raw))
    key.write_text(text, encoding="utf-8")
    time.sleep(0.3)
    return text, "fetched"


def check(row: dict, role: str) -> dict:
    page, how = fetch(row["source_url"])
    quote = norm(row.get("quote") or "")
    found = bool(page) and bool(quote) and (quote in page or bare(quote) in bare(page))
    words = ROLE_WORDS.get(role, ())
    role_ok = any(w in f" {quote} " for w in words)
    names = [n for n in (row.get("name"), row.get("incoming"), row.get("outgoing")) if n]
    surnames = [norm(n).split()[-1] for n in names]
    name_ok = bool(page) and all(s in page for s in surnames)
    return {"fetched": page is not None, "fetch": how, "quote_found": found,
            "role_words": role_ok, "name_on_page": name_ok,
            "passes": bool(page is not None and found and role_ok and name_ok)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inp", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--kind", choices=("roles", "hc"), default="roles")
    args = ap.parse_args(argv)
    blob = json.loads(args.inp.read_text(encoding="utf-8"))
    report = []
    if args.kind == "roles":
        for item in blob["results"]:
            for i, r in enumerate(item["research"]["rows"]):
                report.append({"season": item["season"], "index": i, "team": r["team"],
                               "role": r["role"], "name": r["name"], "url": r["source_url"],
                               **check(r, r["role"])})
    else:
        for i, c in enumerate((blob.get("research") or {}).get("changes") or []):
            report.append({"season": c["season"], "index": i, "team": c["team"], "role": "HC",
                           "name": c["incoming"], "url": c["source_url"], **check(c, "HC")})
    args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    by = {}
    for r in report:
        k = (r["season"], r["role"])
        by.setdefault(k, [0, 0])
        by[k][0] += r["passes"]
        by[k][1] += 1
    for (s, role), (ok, n) in sorted(by.items()):
        print(f"{s} {role}: {ok}/{n} pass")
    fails = [r for r in report if not r["passes"]]
    why = {}
    for r in fails:
        k = ("not fetched" if not r["fetched"] else "quote not on page" if not r["quote_found"]
             else "role words missing" if not r["role_words"] else "name not on page")
        why[k] = why.get(k, 0) + 1
    print(f"{len(report) - len(fails)}/{len(report)} rows pass; failures: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
