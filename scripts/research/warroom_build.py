"""Build the 1.01 War Room page: board CSV + news notes + tags + room -> one HTML file.

Run:  PYTHONPATH=src python scripts/research/warroom_build.py
Reads data/outputs/draft2026_board.csv (from draft_board_2026.py, incl. the
ph{pick} history-aware survival odds) and the template/logic under
scripts/research/warroom/; writes data/outputs/draft2026_warroom.html (the
file published as the artifact). Page logic: `node --test scripts/research/warroom/`.
"""
import json
import pathlib

import numpy as np
import pandas as pd

from gridiron.paths import OUTPUTS, REPO_ROOT

SCR = REPO_ROOT / "scripts" / "research" / "warroom"
b = pd.read_csv(OUTPUTS / "draft2026_board.csv")
b["adp_rank"] = b.adp.rank(method="first")
b["keep_rank"] = np.minimum(b.rank_vor, b.adp_rank)
skill = b[b.pos.isin(["QB", "RB", "WR", "TE"])].nsmallest(230, "keep_rank")
kd = pd.concat([b[b.pos == "K"].nsmallest(14, "adp"), b[b.pos == "DEF"].nsmallest(14, "adp")])
out = pd.concat([skill, kd]).drop_duplicates("name_key").sort_values("rank_vor")

NOTES = {
 "kayshonboutte": "Named the HOU starter opposite Collins with Higgins (ACL) out and Dell on IR. Beat expected points by 33 last year (regress), but the role is real now.",
 "jahmyrgibbs": "Consensus 1.01 everywhere. Montgomery gone to HOU, backup Pacheco on IR. 25.1 PPG in the 6 games without Montgomery last year.",
 "bijanrobinson": "1.02 by every source. Only knock: 6.5-win Falcons offense; Tua is the ATL starter with Penix out for W1.",
 "christianmccaffrey": "Age 30, 413 touches in 2025, calf tightness = rep management. DATA: players who play on a Q tag with a calf issue score ~70% of normal that week (n=12). Fine for the season, watch the W1 tag.",
 "jamarrchase": "Knee hyperextension late Aug, back at practice, W1 expected. DATA: WR knee returns 87% for 6 games, but this was a scare not an absence. TD regression already in the projection.",
 "pukanacua": "Psoas/core in camp; full speed, no setbacks, plays Thu in Australia. DATA: core/rib returns run at 95%, i.e. no effect beyond normal drift. Q tag is noise here.",
 "jonathantaylor": "Daniel Jones back from Achilles and starting; JT's ceiling rides on him. Floor still elite (double-digit TDs every full year).",
 "jaxonsmithnjigba": "35.8% target share in 2025. Sleeper ADP 8.8 vs FFC 5.9.",
 "amonrastbrown": "Gibbs stack partner; steady.",
 "jamescook": "Top-5 RB price; TD-dependent (11 projected).",
 "derrickhenry": "Age 32. ECR 20 but Sleeper drafters take him at 14. Justice Hill / Rasheen Ali behind him, healthy.",
 "saquonbarkley": "Age 29, 2025 was a down year (13.2 PPG). Sleeper ADP 10.6 vs ECR 16.",
 "devonachane": "Malik Willis is now the MIA QB (Tua to ATL). Rebuild offense, but he IS the offense.",
 "kennethwalker": "Now a Chief. Foot soreness was shoe-related; W1 ready. RBs slide in this room, and he is the top target at the turn: see the Next? column for his live odds.",
 "omarionhampton": "Broke an ankle W5 last year, back. Keaton Mitchell change-of-pace only. Turn target; odds in the Next? column.",
 "ashtonjeanty": "Low-ankle sprain, questionable, tracking to play W1 with managed snaps. DATA: RBs back from an ankle score 77% of their prior PPG over the next 6 games (n=11, t=-2.3); playing on a Q tag costs ~20%. Price in a slow first month; turn target if he slides.",
 "chasebrown": "Sleeper ADP 17 vs FFC 13.6 / ECR 15.",
 "nicocollins": "12.6 PPG in 15 games last year; Sleeper projects 218. The WR to take at the turn if the RBs are gone.",
 "brockbowers": "Elite TE1 by 11 pts over McBride. But THIS ROOM takes TEs early: chaguy2457 (pick 17) took him at 21 last year, glavoile (16) took McBride at 26, MaxSchussler (21) goes TE in R4, SirChadius/pbrady reach for TEs. Gone before 24 in nearly every history-aware sim; a bonus only if he falls.",
 "treymcbride": "TE2. glavoile (pick 16) took him in R3 two years running. Also gone before 24 in the history-aware sims.",
 "joshallen": "QB1 by 20 pts. sallymcbride picks 23 and took him at 29 last year; mikedonutgang and glavoile take QBs 9-12 picks early. A long shot at 24 (Next? column). QB4-QB12 sit within 15 pts, so wait.",
 "lamarjackson": "QB2. ECR 38, Sleeper ADP 34, FFC 56 - the sources disagree.",
 "georgepickens": "Now a Cowboy. Usually there at 24/25 (Next? column).",
 "chrisolave": "Healthy. Jordyn Tyson (rookie 1st-rounder) on IR 4-8 wks, so Olave is the only target hog in NO.",
 "maliknabers": "ACL Oct 2025; questionable, game-time decision W1 vs DAL. DATA: WRs back from a knee run at 87% for the next 6 games; a Q-tag game averages 80%. ECR spread 9-47. Expect a slow ramp.",
 "javontewilliams": "14.1 PPG in 2025 for DAL. Sleeper ADP 31, FFC 28.5.",
 "kyrenwilliams": "Rams plan close to 50/50 with Corum. Fade at his round-3 price; Corum is the value.",
 "breecehall": "Groin strain mid-Aug; questionable but moving well, expected to start W1. DATA: groin returns show no lasting PPG drop (RB 0.97, n=3, weak). Any discount is a buy.",
 "jeremiyahlove": "Rookie, ARI. High-ankle sprain, missed 4 weeks of camp, coach 'feels good' about W1. DATA: RB ankle returns run at 77% of prior PPG for 6 games. Bad OL. Fade at his round-3 price.",
 "dandreswift": "Bears RB1. Left practice with 'a cramp'; Monangai wk-to-wk (knee). Roschon Johnson trending. Value if he slides past 48.",
 "travisetienne": "Now in NO. Kamara (knee) not ready, so Etienne opens as the lead back.",
 "davidmontgomery": "Now a Texan. Goal-line role in a real offense; the sim's favorite RB at 48/49.",
 "buckyirving": "TB lead back; ADP 46.",
 "camskattebo": "Sleeper drafters take him at 35 (ECR 54). Early-down role with Tracy/Singletary on passing downs. Overpriced at ADP; fine at 48+.",
 "quinshonjudkins": "CLE lead back, year 2. Usually still there at 48/49.",
 "rasheerice": "No suspension (Rapoport, Aug 12). Offseason knee surgery + a jail stint. Top-12 upside, real risk. Sleeper ADP 29, FFC 18.",
 "lutherburden": "Grade-1 groin, cleared. Bears WR with Loveland/Odunze; ECR 49, Sleeper ADP 59. The sim's favorite at pick 49.",
 "terrymclaurin": "Age 30. The accurate experts fade him (new target competition); usually there at 48/49.",
 "jamesonwilliams": "Boom/bust WR2 in DET. FFC 37, Sleeper 57.",
 "christianwatson": "GB WR1 role; almost always there at 48/49.",
 "mikeevans": "Now a 49er, age 33. Two quad injuries + groin in camp; questionable but expected Thu. Sleeper proj 185 vs ECR-implied 162.",
 "emekaegbuka": "Toe sprain (not turf toe); returned Mon, trending to play. DATA: foot/toe returns show no lasting drop (0.98) but Q-tag games with foot/toe run ~70%. Godwin is the pivot if he sits.",
 "davanteadams": "Age 33, LAR. ADP falling.",
 "romeodunze": "Right leg, sat Mon, precautionary.",
 "jadarianprice": "SEA 1st-round rookie; Charbonnet on PUP (ACL) until at least W5. Clear RB1 for a month-plus.",
 "bhayshultuten": "JAX; questionable tag on Sleeper.",
 "brianthomas": "WR3 in JAX 2-WR sets behind Meyers and Washington; comes off for Hunter packages. Talent bet only.",
 "parkerwashington": "JAX starter in 11 and 12 personnel; targets over Thomas per camp.",
 "marvinharrison": "Year 3, ARI. ECR 73.",
 "rhamondrestevenson": "NE lead back while Henderson (ankle) is out.",
 "treveyonhenderson": "Out since Aug 24 (ankle); unlikely W1 (Wed game). DATA: RB ankle returns run at 77% for 6 games. Upside stash only.",
 "marshawnlloyd": "GB RB1 while Jacobs sits on the exempt list (two misdemeanors pending). ADP jumped 66 spots in a week. The sim's favorite at 96/97.",
 "joshjacobs": "Commissioner's exempt list; cannot practice or play until resolved. Late stash only.",
 "samlaporta": "Hip; back at practice, snap count managed W1. DATA: hip returns are not worse (1.2, n=11), so the discount is only the snap count.",
 "tuckerkraft": "ACL cleared, no restrictions; Jonnu Smith signed behind him.",
 "georgekittle": "Achilles (Jan), age 32, off PUP, limited snaps W1. Too few Achilles returns in 2023-25 to measure; treat as a half-season player.",
 "tylerwarren": "Groin was minor; unrestricted.",
 "colstonloveland": "Year-2 Bears TE, TE3 by projection; ECR sd 12 (experts split).",
 "patrickmahomes": "ACL/LCL Dec 2025. Full participant since July, says on track for W1. QB15 price; run-heavy plan early.",
 "jaydendaniels": "QB6 by projection. The QB window is 72/73; odds in the Next? column.",
 "jalenhurts": "QB4 by projection; 2025's biggest draft win in this league (bogeman, R3). Window 72/73.",
 "trevorlawrence": "QB7 by projection; the QB the sim actually lands at 96 if you wait.",
 "calebwilliams": "QB8 by projection; usually there at 72/73.",
 "kylemonangai": "Hyperextended knee; wk-to-wk, maybe W2.",
 "michaelpenix": "Inactive W1 (knee). Tua starts for ATL.",
 "jordyntyson": "NO rookie WR; short-term IR, 4-8 weeks. Do not draft.",
 "tankdell": "HOU: placed on IR, out at least 4 games. Do not draft. Jaylin Noel takes the slot, Boutte starts outside opposite Collins.",
 "devaughnvele": "NO WR2 while Tyson is on IR; streamer.",
 "roschonjohnson": "Most-added player on Sleeper (48h) after Swift's practice exit. Handcuff.",
 "jonathonbrooks": "Two ACLs; CAR 'very optimistic'. ADP 88, sleeper appeal.",
 "chubahubbard": "Hamstring in Aug; full go for W1.",
 "blakecorum": "Rams moving toward 50/50; RB36 PPG from W7 on last year. Value at ADP about 100.",
 "isiahpacheco": "DET, on IR. Do not draft.",
 "jaydenhiggins": "Torn ACL, out for 2026. Do not draft.",
 "rickypearsall": "Season-ending IR. Do not draft.",
 "kylermurray": "Named MIN starter over McCarthy; Jefferson's QB.",
 "danieljones": "IND starter, back from Achilles.",
 "malikwillis": "MIA starter on a 3-yr, $67.5M deal.",
 "keatonmitchell": "Back at practice Mon after 6 missed; change-of-pace behind Hampton.",
 "alvinkamara": "Knee; not ready. Etienne leads NO.",
 "justicehill": "Henry's backup, healthy.",
}

# Chips shown next to the name. usage = 2025 expected-points screen; experts =
# the multi-year-accurate rankers sit above ADP; avoid = market ahead of both
# usage and experts, or unavailable; regress = beat expected points by 35+.
TAGS = {
    "usage": ["wandalerobinson", "michaelwilson", "kennygainwell", "jakobimeyers", "quentinjohnston", "ricodowdle",
              "rjharvey", "alecpierce", "kylepitts", "romeodoubs", "michaelpittman", "jakeferguson", "woodymarks",
              "deebosamuel", "marshawnlloyd", "travisetienne", "rhamondrestevenson", "jadarianprice", "joshdowns",
              "malikwashington"],
    "experts": ["breecehall", "jonathonbrooks", "djmoore", "isaiahlikely", "lutherburden", "jordanmason",
                "rashidshaheed", "jordanaddison", "cjstroud", "tonypollard", "jacorycroskeymerritt"],
    "avoid": ["bhayshultuten", "matthewgolden", "joshjacobs", "tankdell", "jordyntyson", "isiahpacheco", "kalebjohnson",
              "courtlandsutton", "matthewstafford", "jalennailor", "cooperkupp", "jaydenhiggins", "rickypearsall"],
    "regress": ["pukanacua", "jaxonsmithnjigba", "jonathantaylor", "devonachane", "tuckerkraft", "daltonkincaid",
                "zayflowers", "teehiggins", "stefondiggs", "kayshonboutte", "georgekittle", "dallasgoedert"],
}
TAG_OF = {k: t for t, ks in TAGS.items() for k in ks}


def f(v, nd=1):
    return round(float(v), nd) if pd.notna(v) else None
def i(v):
    return int(v) if pd.notna(v) else None

recs = []
for _, r in out.iterrows():
    recs.append(dict(
        id=str(r["sleeper_id"]) if pd.notna(r["sleeper_id"]) else r.name_key, key=r.name_key, name=r["name"], pos=r.pos,
        team=r.team if pd.notna(r.team) else "", bye=i(r.bye), proj=f(r.proj), vor=f(r.vor), vorw=f(r.vor_waiver),
        tier=i(r.tier), ecr=f(r.rank_ave), ecr_sd=f(r.rank_std), ecr_min=i(r.rank_min), ecr_max=i(r.rank_max),
        adp=f(r.adp), adp_sd=f(r.adp_sd, 2), adp_sl=f(r.adp_half), adp_ffc=f(r.ffc_adp), posrank=int(r.pos_rank_proj),
        inj=r.injury if pd.notna(r.injury) else None, injpart=r.injury_part if pd.notna(r.injury_part) else None,
        ppg25=f(r.ppg_2025), g25=i(r.g_2025), age=i(r.age), note=NOTES.get(r.name_key),
        tag=TAG_OF.get(r.name_key),
        ph={int(c[2:]): f(r[c], 3) for c in b.columns if c.startswith("ph") and c[2:].isdigit() and pd.notna(r[c])} or None,
    ))
meta = dict(repl={"QB": 292.2, "RB": 138.6, "WR": 139.0, "TE": 128.6, "K": 104.2, "DEF": 87.0},
            my_picks=[1, 24, 25, 48, 49, 72, 73, 96, 97, 120, 121, 144, 145, 168, 169],
            generated="2026-09-08 5:15 PM ET",
            sources="Sleeper projections + ADP, FantasyPros ECR (9/08), FFC ADP (12-team half-PPR, 9/3-9/8, 1,837 drafts), nflverse 2025")
# The room: one line per manager from 2023-25 league history (analyze_competition.py).
ROOM = [
    dict(slot=1, name="Kejjeh (you)", picks="1 · 24 · 25", td="2023 champ. Value drafter (avg 5 picks later than market)."),
    dict(slot=2, name="sallymcbride", picks="2 · 23 · 26", td="<b>Josh Allen threat at 23</b>: took him at 29 last year, QB by R3-4 both years. WR-WR to open. TE R6-7. 2024 champ. Has never made a waiver claim."),
    dict(slot=3, name="bogeman", picks="3 · 22 · 27", td="QB R3-4 (Hurts at 36 last year), TE late. Most active manager: 36 waiver claims, $285 FAAB, 3 trades. 4-10 then 7-7."),
    dict(slot=4, name="MaxSchussler", picks="4 · 21 · 28", td="<b>TE early</b>: round 4 each of the last two years (Kittle at 40), 18 picks ahead of market. QB late. RB-RB to open in 2023-24."),
    dict(slot=5, name="LaisterSmith", picks="5 · 20 · 29", td="One season. WR R1, QB R3 (Daniels at 35), TE R9. Most rookie-heavy drafter (20% of picks)."),
    dict(slot=6, name="mikedonutgang200", picks="6 · 19 · 30", td="Defending champ, 12-2, 125 PPG. WR in R1 every year, QB R3-4 (9 picks early), TE R5-7. Never uses waivers."),
    dict(slot=7, name="clyvejohnson", picks="7 · 18 · 31", td="RB in R1 every year, then QB R4-6 (Burrow at 43), TE R7-9. Drafts at market, never uses waivers. 10-4 last year."),
    dict(slot=8, name="chaguy2457", picks="8 · 17 · 32", td="<b>Biggest Bowers threat</b>: took him at 21 (R2, 13 picks early) last year. Waits on QB (R11). 9-5, spent his full $100 FAAB."),
    dict(slot=9, name="glavoile", picks="9 · 16 · 33", td="<b>TE in R3 two years running</b> (McBride at 26). WR in R1 every year. QB 12 picks early. Reach-prone. 3-11 last year."),
    dict(slot=10, name="reijcage", picks="10 · 15 · 34", td="WR-WR to open, QB late (R4-6, 10 picks after market), TE R7-8. Best PPG in league history (123). 10-4 in 2024."),
    dict(slot=11, name="pbrady98", picks="11 · 14 · 35", td="RB in R1 every year (Saquon 1.01 last year). TE R5-7 but 16 picks early (LaPorta R5). Takes two QBs by R10. Reach-prone. 5-9."),
    dict(slot=12, name="SirChadius", picks="12 · 13 · 36", td="Was coochiemoocher22. <b>Biggest reacher</b> in the league (Pitts at 70, 84 picks early; TE in R2 in 2023). QB late. Active: 32 claims, $153 FAAB."),
]
meta["room"] = ROOM
data = dict(meta=meta, players=recs)
(OUTPUTS / "draft2026_warroom_data.json").write_text(json.dumps(data), encoding="utf-8")
tpl = (SCR / "draftroom_template.html").read_text(encoding="utf-8")
logic = (SCR / "draftroom_logic.js").read_text(encoding="utf-8")
html = tpl.replace("/*__DATA__*/", json.dumps(data, separators=(",", ":"))).replace("/*__LOGIC__*/", logic)
assert "/*__" not in html, "unfilled template placeholder"
(OUTPUTS / "draft2026_warroom.html").write_text(html, encoding="utf-8")
print(len(recs), "players;", sum(1 for r in recs if r["note"]), "with notes;", out.pos.value_counts().to_dict())
print("notes unmatched:", [k for k in NOTES if k not in set(out.name_key)])
print("tags unmatched:", [k for k in TAG_OF if k not in set(out.name_key)])
print("tag counts:", {t: sum(1 for r in recs if r["tag"] == t) for t in TAGS})
print("html bytes:", len(html.encode("utf-8")))
