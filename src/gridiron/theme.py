"""The one design system both pages share, and the scripts they share.

Game Day and the board are two files rendered by two modules; the reader
sees one product. This module holds what makes that true: the colour and
type tokens (a flat navy/charcoal foundation, one lime accent reserved
for the primary state and cyan for links, a fixed type scale, tabular
numerals; a bottom tab bar on phones), the component rules both
pages use (cards, badges, tables, disclosures, controls), the three-page
navigation (Game Day · Board · Free Agents), the dated header strip, and
two small inline scripts:

  * `AGES_JS` re-states every dated element's age at the present instant,
    so "league snapshot 12 min ago" stays true while the tab sits open. It
    fetches nothing. An age is a label, not a gate — so:
  * `VALIDITY_JS` re-judges, on the reader's clock, the two things a build
    fixes at build time and the clock then overtakes: the instant the
    evidence behind the page's moves expires (`gridiron-valid-until`, from
    the gate's own cadences) and each move's kickoff deadline
    (`data-deadline`). Past either, the element is marked and its move is
    withdrawn in words the build wrote; nothing is recomputed and nothing
    is fetched.
  * `SNAPSHOT_JS` asks the HOSTING server, every five minutes while the tab
    is visible, whether a newer build of THIS page has been published, by a
    conditional GET of the page's own URL against the ETag it last saw. A
    newer build is announced with a Reload control; nothing is swapped
    underneath the reader, and the reader's filters and scroll position
    are carried across the reload. Failures back off (doubling, capped at
    30 minutes) and keep the page usable exactly as it was; a 429 or an
    offline device is a status line, never a blank page. There is one
    timer, cleared before it is re-armed, and the check pauses while the
    tab is hidden. It runs only on an http(s) origin: a file opened from
    disk has no server to ask, and the status line says so. It never
    implies the schedule is real time: a build is whatever the last
    successful cloud run published, dated by its own stamp.

No dependency, no CDN, no font download, no telemetry: everything here is
inline text the renderer writes into the page. Every string from data
reaches the DOM through textContent.
"""
from __future__ import annotations

import html
from datetime import timezone

#: The three pages as the navigation names them, with the file each opens.
#: Fragments are allowed by the publication allowlist (the link resolves to
#: an allowlisted page); the board's Free Agents section carries the id.
NAV: tuple[tuple[str, str, str], ...] = (
    ("gameday", "Game Day", "gameday_latest.html"),
    ("board", "Board", "dashboard_latest.html"),
    ("radar", "Free Agents", "dashboard_latest.html#free-agents"),
)

#: The <meta> that carries the build stamp the snapshot check compares.
BUILD_META = "gridiron-build"

#: The <meta> that carries the instant the page's gated evidence expires.
VALID_META = "gridiron-valid-until"

CSS = """
:root{color-scheme:dark;--bg:#0a0f1c;--bg2:#0e1526;--card:#111a2e;--card2:#152039;--line:#222d47;
--line2:#34416a;--fg:#eef2fb;--muted:#b0bad0;--dim:#8f9ab4;--lime:#c9ff4f;--lime-ink:#0a0f1c;
--cyan:#72dcff;--ok:#9cf29a;--warn:#ffc86b;--bad:#ff8a8a;--info:#72dcff;--chip:#18223d;
--focus:#72dcff;--radius:16px;--radius-s:10px;--pad:16px;
--t-xs:12px;--t-s:13.5px;--t-b:15.5px;--t-l:18px}
*{box-sizing:border-box}html,body{max-width:100%}
html{background:var(--bg)}
body{margin:0;padding:0 16px 56px;background:var(--bg);color:var(--fg);
font:var(--t-b)/1.55 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
overflow-wrap:anywhere;word-break:break-word;-webkit-font-smoothing:antialiased}
main{max-width:1180px;margin:0 auto}
[id]{scroll-margin-top:76px}
a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:27px;line-height:1.15;letter-spacing:-.02em;margin:6px 0 12px;font-weight:800}
h2{font-size:19px;line-height:1.25;letter-spacing:-.01em;margin:44px 0 12px;padding-top:20px;
border-top:1px solid var(--line);font-weight:750}
h3{font-size:16.5px;line-height:1.3;margin:14px 0 6px;font-weight:700;letter-spacing:-.005em}
p{margin:6px 0}.sub{color:var(--muted)}.small{font-size:var(--t-s)}.dim{color:var(--dim)}
b,strong{font-weight:700}
.num,td.num,.pts{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.info{color:var(--info)}.lime{color:var(--lime)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px var(--pad);margin:12px 0}
.card.quiet{background:var(--bg2)}
.badge,.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:var(--t-xs);font-weight:700;
letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--line2);background:var(--chip);
color:var(--fg);white-space:nowrap;vertical-align:middle}
.badge.go,.pill.ok{color:var(--lime);border-color:rgba(201,255,79,.45)}
.badge.cond{color:var(--cyan);border-color:rgba(114,220,255,.45)}
.badge.held,.badge.check,.pill.warn{color:var(--warn);border-color:rgba(255,200,107,.45)}
.badge.now,.pill.bad{color:var(--bad);border-color:rgba(255,138,138,.5)}
.badge.today,.pill.info{color:var(--cyan);border-color:rgba(114,220,255,.4)}
.badge.opt{color:var(--muted)}
.pill.solid{background:var(--lime);color:var(--lime-ink);border-color:transparent}
.banner{border:1px solid var(--line);border-left:3px solid var(--bad);padding:12px 14px;margin:12px 0;background:var(--card);
border-radius:var(--radius-s)}
.banner ul{margin:6px 0 0}.banner li{font-size:var(--t-s);color:var(--muted)}
.banner.ok{border-left-color:var(--lime)}.banner.warn{border-left-color:var(--warn)}
.gatebox{border:1px dashed rgba(255,200,107,.6);border-radius:var(--radius-s);padding:10px 12px;margin:10px 0;color:var(--warn);font-size:var(--t-s)}
table{border-collapse:collapse;width:100%;font-size:var(--t-s)}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:700;color:var(--dim);white-space:nowrap;font-size:var(--t-xs);letter-spacing:.08em;text-transform:uppercase}
td.num{text-align:right;white-space:nowrap;font-weight:700}
td:first-child,th:first-child{white-space:nowrap}
tr:last-child td{border-bottom:0}
.wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:var(--radius-s)}
details{margin:6px 0}summary{cursor:pointer;color:var(--cyan);padding:6px 0;font-weight:600}
summary:hover{text-decoration:underline}
code,pre{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere;word-break:break-all}
pre{background:var(--bg2);border:1px solid var(--line);padding:10px;border-radius:var(--radius-s);overflow-x:auto;white-space:pre-wrap}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
button,.btn{font:inherit;font-weight:700;padding:9px 16px;border-radius:999px;border:1px solid var(--line2);
background:var(--chip);color:var(--fg);cursor:pointer;min-height:44px;letter-spacing:.01em;display:inline-flex;
align-items:center;justify-content:center;gap:6px}
a.btn:hover{text-decoration:none;border-color:var(--cyan)}
button.primary,.btn.primary{background:var(--lime);color:var(--lime-ink);border-color:transparent}
button[disabled]{opacity:.55;cursor:default}
button:hover:not([disabled]){border-color:var(--cyan)}
input[type=search],select{font:inherit;padding:9px 12px;border-radius:var(--radius-s);border:1px solid var(--line2);
background:var(--bg2);color:var(--fg);min-height:44px}
input[type=search]{width:100%}
:is(button,summary,a,input,select,[tabindex]):focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:6px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:10px 0}
.kpi>div{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius-s);padding:10px 12px;font-size:var(--t-xs);
color:var(--dim);letter-spacing:.06em;text-transform:uppercase;font-weight:700}
.kpi b{display:block;font-size:24px;color:var(--fg);letter-spacing:-.01em;text-transform:none;font-variant-numeric:tabular-nums;margin-top:2px}
.act{border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin:10px 0;
background:var(--card);box-shadow:inset 3px 0 0 var(--lime)}
.act.withheld,.act.off{background:var(--bg2);box-shadow:inset 3px 0 0 var(--line2)}
.act.conditional{box-shadow:inset 3px 0 0 var(--cyan)}
.act.act-NOW{box-shadow:inset 3px 0 0 var(--bad)}.act.act-NOW.withheld{box-shadow:inset 3px 0 0 var(--line2)}
.act h3{margin:6px 0 4px;font-size:17px}.act .bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.act .why{font-size:var(--t-s);color:var(--muted)}.act .deadline{font-weight:700}.act .backup{color:var(--muted);font-size:var(--t-s)}
.nav{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:center;gap:6px;margin:0 -16px 4px;padding:8px 16px;
background:rgba(10,15,28,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.nav a{flex:1 1 0;max-width:200px;text-align:center;padding:10px 6px;min-height:44px;border-radius:999px;color:var(--muted);font-weight:700;
font-size:14px;letter-spacing:.01em;border:1px solid transparent;display:flex;align-items:center;justify-content:center}
.nav a:hover{text-decoration:none;color:var(--fg);border-color:var(--line2)}
.nav a[aria-current=page]{color:var(--fg);background:var(--chip);border-color:var(--line2);box-shadow:inset 0 -2px 0 var(--lime)}
.nav .brand{flex:0 0 auto;font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--fg);padding:0 10px 0 0}
.hero{padding:14px 0 4px}
.eyebrow{font-size:var(--t-xs);letter-spacing:.14em;text-transform:uppercase;color:var(--dim);font-weight:700}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;margin:0;padding:0;border:0}
.statusbar{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 6px}
.chip{display:inline-block;padding:6px 12px;border-radius:999px;background:var(--card);
border:1px solid var(--line);font-size:var(--t-s);color:var(--muted);line-height:1.35}
.chip b{color:var(--fg);font-weight:700}.chip::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;
background:var(--dim);margin-right:7px;vertical-align:1px}
.chip.ok::before{background:var(--ok)}.chip.warn::before{background:var(--warn)}.chip.bad::before{background:var(--bad)}
.chip.warn b{color:var(--warn)}.chip.bad b{color:var(--bad)}
.chip[data-live]{border-style:dashed}.chip[data-live] b{text-decoration:line-through;color:var(--muted)}.chip[data-live]::before{background:var(--warn)}
.meta{margin:8px 0 0;border:1px solid var(--line);border-radius:var(--radius-s);background:var(--bg2);padding:0 14px}
.meta>summary{font-size:var(--t-s);color:var(--muted);min-height:44px;display:flex;align-items:center;font-weight:600}
.meta[open]{padding-bottom:10px}
.ages{display:flex;flex-direction:column;gap:4px;margin:4px 0 8px;font-size:var(--t-s);color:var(--muted)}
.ages span b{color:var(--fg);font-weight:600}
.snap{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0;font-size:var(--t-s)}
.snap .status{flex:1 1 220px;min-height:1.4em;color:var(--muted)}
.snap button{min-height:40px;padding:6px 14px;font-size:var(--t-s)}
.snapbar{display:none;align-items:center;gap:10px;flex-wrap:wrap;border:1px solid rgba(201,255,79,.5);background:rgba(201,255,79,.07);
border-radius:var(--radius-s);padding:10px 12px;margin:8px 0}
.snapbar.show{display:flex}
.hidden{display:none}
.vstate{display:block;margin-top:3px;font-size:var(--t-s);font-weight:700;color:var(--warn);letter-spacing:.01em}
.vstate:empty{display:none}
li[data-live] .badge,[data-live] .badge.v-LINEUP,[data-held] .badge.v-LINEUP{background:transparent;color:var(--muted);border-color:var(--line2);text-decoration:line-through}
[data-live] .stat.plus,[data-held] .stat.plus{color:var(--muted)}
.act[data-live],.dcard[data-live]{background:var(--bg2);box-shadow:inset 3px 0 0 var(--line2);border-style:dashed}
.act[data-live] h3,.dcard[data-live] h3{color:var(--muted)}.act[data-live] .bar .badge,.dcard[data-live] .dhead .badge{opacity:.6;text-decoration:line-through}
.dcard[data-live] a.btn.primary{background:var(--chip);color:var(--fg);border-color:var(--line2)}
#validity{border-left-color:var(--warn)}.banner[data-live]{border-left-color:var(--warn)}.banner[data-live] b.ok{color:var(--muted);text-decoration:line-through}
.headline[data-live] h1{color:var(--muted);text-decoration:line-through;text-decoration-thickness:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chips button{min-height:40px;padding:6px 14px;font-size:var(--t-s)}
.chips button[aria-pressed=true]{background:var(--fg);color:var(--lime-ink);border-color:transparent}
.controls{display:grid;grid-template-columns:1fr;gap:8px;margin:8px 0}
.controls label{font-size:var(--t-xs);color:var(--dim);letter-spacing:.08em;text-transform:uppercase;display:block;margin-bottom:4px;font-weight:700}
.stat{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.stat.plus{color:var(--lime)}.kpi b.lime{color:var(--lime)}.stat.minus{color:var(--muted)}
.sect{margin:28px 0 0;border-top:1px solid var(--line)}
.sect>summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;padding:10px 0;color:var(--fg)}
.sect>summary::-webkit-details-marker{display:none}.sect>summary:hover{text-decoration:none}
.sect>summary h2{margin:0;padding:0;border:0;font-size:17px}
.sect>summary .hint{font-size:var(--t-s);color:var(--muted);font-weight:500;text-align:right;margin-left:auto}
.sect>summary::after{content:"Show";font-size:var(--t-s);color:var(--cyan);font-weight:700;flex:0 0 auto}
.sect[open]>summary::after{content:"Hide"}
.panel{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin:12px 0}
.panel h3{font-size:var(--t-xs);letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin:0 0 8px}
.panel p,.panel li{font-size:var(--t-s)}
@media (min-width:700px){.controls{grid-template-columns:2fr 1fr 1fr}body{padding:0 24px 64px}h1{font-size:32px}}
@media (min-width:1100px){body{padding:0 32px 80px}}
@media (max-width:560px){h1{font-size:25px}.card{padding:14px 14px}.act{padding:13px 14px}
.nav{position:fixed;top:auto;bottom:0;left:0;right:0;margin:0;
padding:6px max(8px,env(safe-area-inset-right)) calc(10px + env(safe-area-inset-bottom)) max(8px,env(safe-area-inset-left));
border-top:1px solid var(--line);border-bottom:0;gap:4px}
.nav a{font-size:13.5px;padding:10px 2px;min-height:48px}.nav .brand{display:none}
body{padding-bottom:calc(96px + env(safe-area-inset-bottom))}[id]{scroll-margin-top:12px}.hero{padding-top:8px}
.statusbar{gap:4px 14px;margin:2px 0 4px}.chip{font-size:13px;padding:2px 0;background:none;border:0;border-radius:0}.chip.bad{padding:4px 10px;border:1px solid rgba(255,128,128,.45);border-radius:999px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
@media print{.nav,button,.snap{display:none}details{display:block}details>*{display:block}body{background:#fff;color:#000}}
"""


def _e(x: object) -> str:
    return html.escape("" if x is None else str(x))


def nav_html(active: str) -> str:
    """The three-page navigation. `active` names the page being rendered
    so the current tab carries aria-current and takes the accent."""
    out = ["<nav class=\"nav\" aria-label=\"Pages\"><span class=\"brand\">gridiron</span>"]
    for key, label, href in NAV:
        cur = " aria-current=\"page\"" if key == active else ""
        out.append(f"<a href=\"{_e(href)}\"{cur}>{_e(label)}</a>")
    out.append("</nav>")
    return "".join(out)


def build_meta(stamp: str, page: str) -> str:
    """The stamp the snapshot check compares: the build's own generated time,
    ISO-8601 UTC, and the page's own filename."""
    return f"<meta name=\"{BUILD_META}\" content=\"{_e(stamp)}\" data-page=\"{_e(page)}\">"


def age_span(label: str, iso: str | None, text: str) -> str:
    """A dated element the AGES script keeps current. `text` is what the
    build wrote; the script appends the live age."""
    stamp = f" data-asof=\"{_e(iso)}\"" if iso else ""
    return f"<span{stamp}><b>{_e(label)}</b> {_e(text)}<span class=\"age\"></span></span>"


def valid_meta(until: str, why: str) -> str:
    """The instant (ISO-8601 UTC) at which the evidence behind this page's
    moves expires, and a sentence naming the source that expires first."""
    return f"<meta name=\"{VALID_META}\" content=\"{_e(until)}\" data-why=\"{_e(why)}\">"


def validity_html() -> str:
    """The banner VALIDITY_JS fills once a deadline or the evidence passes.
    Hidden until then; without the script it never appears."""
    return ("<div class=\"banner\" id=\"validity\" role=\"status\" aria-live=\"polite\" hidden>"
            "<b id=\"validity-head\"></b> <span id=\"validity-text\"></span></div>")


def snapshot_html() -> str:
    """The snapshot-check strip and its banner, together (see the two
    halves below: a page may place them apart)."""
    return snapshot_strip_html() + snapshot_banner_html()


def snapshot_banner_html() -> str:
    """The banner that appears when a newer build is published. Always
    placed where the reader will see it, never inside a disclosure."""
    return ("<div class=\"snapbar\" id=\"snap-banner\" role=\"status\" aria-live=\"polite\">"
            "<span id=\"snap-banner-text\"></span>"
            "<button type=\"button\" class=\"primary\" id=\"snap-reload\">Reload this page</button>"
            "</div>")


def snapshot_strip_html() -> str:
    """The check's status line, Check now and Pause."""
    return (
        "<div class=\"snap\" id=\"snap\">"
        "<span class=\"status\" id=\"snap-status\" role=\"status\" aria-live=\"polite\">"
        "Published-build check: needs the hosted site and its script.</span>"
        "<button type=\"button\" id=\"snap-check\" disabled>Check now</button>"
        "<button type=\"button\" id=\"snap-pause\" aria-pressed=\"false\" disabled>Pause</button>"
        "</div>")


def time_html(t, text: str) -> str:
    """A time the AGES script re-states in the reader's own time zone; the
    build's UTC wording stays when the script cannot run."""
    iso = t.astimezone(timezone.utc).isoformat(timespec="seconds")
    return f"<time datetime=\"{_e(iso)}\" data-local=\"\">{_e(text)}</time>"


def meta_details(inner: str, summary: str = "Data & freshness") -> str:
    """Provenance the reader may want but does not need first: ages, the
    published-build check, the model label. Warnings never go in here."""
    return (f"<details class=\"meta\" id=\"data-freshness\"><summary>{_e(summary)}</summary>"
            f"{inner}</details>")


#: Re-states ages. Runs everywhere, fetches nothing.
AGES_JS = r"""
(function(){
'use strict';
function ageText(ms,nowMs){ var s=Math.max(0,(nowMs-ms)/1000);
  if(s<90) return Math.floor(s)+' s ago'; if(s<5400) return Math.floor(s/60)+' min ago';
  if(s<172800) return (s/3600).toFixed(1)+' h ago'; return (s/86400).toFixed(1)+' days ago'; }
function tick(){ var nodes=document.querySelectorAll('[data-asof]'), now=Date.now();
  for(var i=0;i<nodes.length;i++){ var t=Date.parse(nodes[i].getAttribute('data-asof')||''), a=nodes[i].querySelector('.age');
    if(!a) continue; a.textContent=isNaN(t)?'':(t-now>120000?' · dated AFTER this device\u2019s clock — the clock or the stamp is wrong':' · '+ageText(t,now)); } }
var fmt=null; try{ fmt=new Intl.DateTimeFormat(undefined,{weekday:'short',hour:'numeric',minute:'2-digit',timeZoneName:'short'}); }catch(e){}
function local(){ if(!fmt) return; var ts=document.querySelectorAll('time[data-local]');
  for(var i=0;i<ts.length;i++){ var t=Date.parse(ts[i].getAttribute('datetime')||''); if(isNaN(t)) continue;
    if(!ts[i].getAttribute('title')) ts[i].setAttribute('title',ts[i].textContent); ts[i].textContent=fmt.format(new Date(t)); } }
local(); tick(); if(typeof setInterval==='function') setInterval(tick,30000);
window.gridironAges={tick:tick,local:local};
})();
"""

#: Deadlines and evidence expiry, re-judged on the reader's clock. Every
#: sentence it shows was written by the build into an attribute.
VALIDITY_JS = r"""
(function(){
'use strict';
var meta=document.querySelector('meta[name="gridiron-valid-until"]');
var UNTIL=meta?Date.parse(meta.getAttribute('content')||''):NaN, WHY=meta?(meta.getAttribute('data-why')||''):'';
var box=document.getElementById('validity'), head=document.getElementById('validity-head'), text=document.getElementById('validity-text');
var S={skewMs:0,expired:false,lapsed:0,last:null};
function stamp(ms){ return new Date(ms).toISOString().replace('T',' ').slice(0,16)+' UTC'; }
function tick(nowMs){
  var now=(typeof nowMs==='number')?nowMs:Date.now()+S.skewMs, expired=!isNaN(UNTIL)&&now>=UNTIL, lapsed=0, changed=false;
  var nodes=document.querySelectorAll('[data-deadline],[data-gated],[data-held]');
  for(var i=0;i<nodes.length;i++){ var el=nodes[i], dl=Date.parse(el.getAttribute('data-deadline')||'');
    var isLapsed=!isNaN(dl)&&now>=dl, state=isLapsed?'lapsed':(expired&&el.hasAttribute('data-gated'))?'expired':'';
    if((el.getAttribute('data-live')||'')!==state){ changed=true; if(state) el.setAttribute('data-live',state); else el.removeAttribute('data-live'); }
    var built=el.getAttribute('data-verdict-built');
    if(built!==null){ var v=isLapsed?(el.getAttribute('data-lapse-verdict')||built):built; if(el.getAttribute('data-verdict')!==v){ el.setAttribute('data-verdict',v); changed=true; } }
    var note=el.querySelector('.vstate'), words=state==='lapsed'?el.getAttribute('data-lapse-text'):state==='expired'?el.getAttribute('data-expire-text'):el.getAttribute('data-held');
    if(note&&note.textContent!==(words||'')) note.textContent=words||'';
    if(isLapsed) lapsed+=1; }
  var counts=document.querySelectorAll('[data-live-count]');
  for(var j=0;j<counts.length;j++){ var want=counts[j].getAttribute('data-live-count');
    var n=document.querySelectorAll('li.rrow[data-verdict="'+want+'"]').length;
    if(counts[j].textContent!==String(n)) counts[j].textContent=String(n); }
  S.expired=expired; S.lapsed=lapsed; S.last=now;
  if(box&&head&&text){ if(expired||lapsed){ box.hidden=false;
      head.textContent=expired?'Evidence expired '+stamp(UNTIL)+'.':'Kickoff passed for '+lapsed+' item(s) on this page.';
      text.textContent=(expired?WHY+' Every move below is now the last known picture, not advice. ':'')
        +(lapsed?lapsed+' item(s) are past their kickoff deadline and are marked in place. ':'')
        +'This page cannot rebuild itself: reload once a newer build is published, or check Sleeper directly.'; }
    else box.hidden=true; }
  if(changed&&window.gridironRadar&&window.gridironRadar.apply) window.gridironRadar.apply();
  return {expired:expired,lapsed:lapsed};
}
tick(); if(typeof setInterval==='function') setInterval(function(){ tick(); },30000);
document.addEventListener('visibilitychange',function(){ if(!document.hidden) tick(); });
window.gridironValidity={tick:tick,state:function(){ return S; }};
})();
"""

#: The published-build check. See the module docstring.
SNAPSHOT_JS = r"""
(function(){
'use strict';
var meta=document.querySelector('meta[name="gridiron-build"]'); if(!meta) return;
var OWN=meta.getAttribute('content')||'', PAGE=meta.getAttribute('data-page')||'';
var st=document.getElementById('snap-status'), btn=document.getElementById('snap-check'), pause=document.getElementById('snap-pause'),
    bar=document.getElementById('snap-banner'), barText=document.getElementById('snap-banner-text'), reload=document.getElementById('snap-reload');
if(!st||!btn||!pause||!bar) return;
var S={enabled:false,timer:null,inflight:false,failures:0,checks:0,lastCheckAt:null,lastResult:'',etag:null,paused:false,
  newer:null,nextMs:null,intervalMs:300000,backoffMaxMs:1800000,timeoutMs:10000,throttleMs:8000,lastManualAt:null};
function set(text){ st.textContent=text; }
function fmt(iso){ if(!iso) return 'unknown time'; var d=new Date(iso); if(isNaN(d)) return String(iso); return d.toISOString().replace('T',' ').slice(0,16)+' UTC'; }
function mins(ms){ return Math.max(1,Math.round(ms/60000))+' min'; }
function clearTimer(){ if(S.timer){ clearTimeout(S.timer); S.timer=null; } S.nextMs=null; }
function schedule(ms){ clearTimer(); if(!S.enabled||S.paused||S.newer||document.hidden) return;
  S.nextMs=ms; S.timer=setTimeout(function(){ check(false); },ms); }
function saveScroll(){ try{ sessionStorage.setItem('gridiron:scroll:'+PAGE,String(window.scrollY||window.pageYOffset||0)); }catch(e){} }
function restoreScroll(){ try{ var y=sessionStorage.getItem('gridiron:scroll:'+PAGE); if(y!==null){ sessionStorage.removeItem('gridiron:scroll:'+PAGE); window.scrollTo(0,parseInt(y,10)||0); } }catch(e){} }
function announce(stamp){ S.newer=stamp; clearTimer();
  barText.textContent='A newer build of this page was published ('+fmt(stamp)+'; this page is '+fmt(OWN)+'). Reload when you are ready — your filters and reading position are kept.';
  bar.className='snapbar show'; set('Newer build available since '+fmt(stamp)+'. Checks paused until you reload.'); }
function check(manual){
  if(S.inflight) return Promise.resolve('inflight');
  var nowMs=Date.now();
  if(manual&&S.lastManualAt&&nowMs-S.lastManualAt<S.throttleMs){ set('A check just ran; wait a few seconds.'); return Promise.resolve('throttled'); }
  if(manual) S.lastManualAt=nowMs;
  S.inflight=true; S.checks+=1; btn.disabled=true; set('Checking the hosting server for a newer build…');
  var ctl=(typeof AbortController==='function')?new AbortController():null, timer=null;
  var headers={}; if(S.etag) headers['If-None-Match']=S.etag;
  var opts={cache:'no-store',credentials:'omit',headers:headers}; if(ctl) opts.signal=ctl.signal;
  var url=location.pathname+(location.search||'');
  var work=fetch(url,opts).then(function(r){
    if(r.status===304) return {ok:true,unchanged:true,status:304};
    if(!r.ok) return {ok:false,status:r.status,error:'HTTP '+r.status};
    var etag=r.headers.get('etag');
    return r.text().then(function(t){ var m=/<meta name="gridiron-build" content="([^"]*)" data-page="([^"]*)">/.exec(t);
      return {ok:true,unchanged:false,status:r.status,etag:etag,stamp:m?m[1]:'',page:m?m[2]:null}; },function(){ return {ok:false,status:r.status,error:'unreadable body'}; });
  },function(e){ return {ok:false,status:0,error:(e&&e.name==='AbortError')?'timeout after '+S.timeoutMs+' ms':((e&&e.message)||'network error')}; });
  var late=new Promise(function(resolve){ timer=setTimeout(function(){ if(ctl) ctl.abort(); resolve({ok:false,status:0,error:'timeout after '+S.timeoutMs+' ms'}); },S.timeoutMs); });
  return Promise.race([work,late]).then(function(res){ clearTimeout(timer); S.inflight=false; btn.disabled=false; S.lastCheckAt=Date.now();
    if(!res.ok){ S.failures+=1; S.lastResult='failed: '+res.error; var wait=Math.min(S.backoffMaxMs,S.intervalMs*Math.pow(2,S.failures-1));
      set('Check failed ('+res.error+'). This page is unchanged and still readable as dated; its moves are judged on their own deadlines and evidence. Next try in '+mins(wait)+(res.status===429?' (the server asked for a slower pace)':'')+'.');
      schedule(wait); return 'failed'; }
    S.failures=0;
    if(res.unchanged){ S.lastResult='unchanged'; set('No newer build published (checked '+fmt(new Date().toISOString())+'; the server still serves the build of '+fmt(OWN)+'). Next check in '+mins(S.intervalMs)+'.'); schedule(S.intervalMs); return 'unchanged'; }
    if(res.page!==null&&res.page!==PAGE){ S.lastResult='wrong page '+res.page; set('The server answered with a different page ('+res.page+') at this address, a wrong page for this check; nothing is assumed from it. Next check in '+mins(S.intervalMs)+'.'); schedule(S.intervalMs); return 'wrongpage'; }
    var ts=Date.parse(res.stamp), to=Date.parse(OWN);
    if(!res.stamp||isNaN(ts)||isNaN(to)){ S.lastResult='no stamp'; set('The server returned a page without a readable build stamp; nothing is assumed from it. Next check in '+mins(S.intervalMs)+'.'); schedule(S.intervalMs); return 'nostamp'; }
    if(res.etag) S.etag=res.etag;
    if(ts>to){ S.lastResult='newer '+res.stamp; announce(res.stamp); return 'newer'; }
    if(ts<to){ S.lastResult='older '+res.stamp; set('The server serves an OLDER build ('+fmt(res.stamp)+') than this page ('+fmt(OWN)+'): a deploy may be in flight or a cache lagging. Nothing to do. Next check in '+mins(S.intervalMs)+'.'); schedule(S.intervalMs); return 'older'; }
    S.lastResult='same'; set('This is the latest published build ('+fmt(OWN)+'; checked '+fmt(new Date().toISOString())+'). Next check in '+mins(S.intervalMs)+'.'); schedule(S.intervalMs); return 'same'; });
}
if(!window.fetch||!/^https?:$/.test(location.protocol)){
  set('Published-build check is off: this copy was opened from a file, not from the hosted site, so there is no server to ask. The page is dated by its own header.');
  return; }
S.enabled=true; btn.disabled=false; pause.disabled=false;
btn.addEventListener('click',function(){ check(true); });
pause.addEventListener('click',function(){ S.paused=!S.paused; pause.setAttribute('aria-pressed',S.paused?'true':'false'); pause.textContent=S.paused?'Resume':'Pause';
  if(S.paused){ clearTimer(); set('Checks paused. Check now still works.'); } else { set('Checks resumed.'); schedule(1000); } });
reload.addEventListener('click',function(){ saveScroll(); location.reload(); });
window.addEventListener('offline',function(){ if(S.enabled&&!S.inflight) set('This device is offline. The page is unchanged and dated below; checks resume when it reconnects.'); });
window.addEventListener('online',function(){ if(!S.enabled||S.paused||S.newer) return; set('Back online; checking for a newer build shortly.'); schedule(1000); });
document.addEventListener('visibilitychange',function(){ if(document.hidden){ clearTimer(); return; }
  if(!S.enabled||S.paused||S.newer) return; var since=S.lastCheckAt?Date.now()-S.lastCheckAt:Infinity; schedule(since>=S.intervalMs?1000:S.intervalMs-since); });
restoreScroll();
set('This page is the build of '+fmt(OWN)+'. The hosting server is asked every '+mins(S.intervalMs)+' while this tab is visible whether a newer build was published; a cloud run publishes one at best every 15 minutes, with no guarantee of timing.');
schedule(S.intervalMs);
window.gridironSnapshot={check:check,state:function(){ return S; },tune:function(o){ o=o||{}; if(typeof o.intervalMs==='number'&&o.intervalMs>0) S.intervalMs=o.intervalMs;
  if(typeof o.timeoutMs==='number'&&o.timeoutMs>0) S.timeoutMs=o.timeoutMs; if(typeof o.backoffMaxMs==='number') S.backoffMaxMs=o.backoffMaxMs; if(typeof o.throttleMs==='number') S.throttleMs=o.throttleMs; schedule(S.intervalMs); return S; }};
})();
"""


#: The viewport both pages declare. `viewport-fit=cover` lets the phone tab
#: bar reach the screen edge on an iPhone with a home indicator; the CSS
#: then pads it by `env(safe-area-inset-bottom)` so no tab sits under it.
VIEWPORT = "width=device-width, initial-scale=1, viewport-fit=cover"


def page_copy(html_text: str) -> str:
    """The words a reader can see or hear: the page with its stylesheets and
    inline style attributes removed. For wording guards ("never call a lead
    safe"): a CSS identifier such as `env(safe-area-inset-bottom)` is not a
    claim, while script strings stay in, because the page's own script
    writes visible copy."""
    import re
    out = re.sub(r"<style\b[^>]*>.*?</style>", " ", html_text, flags=re.S | re.I)
    return re.sub(r"\sstyle=\"[^\"]*\"", " ", out)
