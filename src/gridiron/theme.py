"""The one design system both pages share, and the scripts they share.

Game Day and the board are two files rendered by two modules; the reader
sees one product. This module holds what makes that true: the colour and
type tokens (a charcoal/navy foundation with restrained lime and cyan
accents, tabular numerals, generous spacing), the component rules both
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
:root{color-scheme:dark;--bg:#0b1020;--bg2:#0f1629;--card:#141d33;--card2:#182342;--line:#25314f;
--line2:#33416a;--fg:#e9eef8;--muted:#a3aec6;--dim:#7f8aa6;--lime:#c9ff4f;--lime-ink:#0b1020;
--cyan:#5fe3ff;--ok:#8ef58a;--warn:#ffc86b;--bad:#ff8080;--info:#5fe3ff;--chip:#1c2745;
--focus:#5fe3ff;--shadow:0 1px 0 rgba(255,255,255,.03) inset,0 10px 30px rgba(0,0,0,.35);
--radius:14px;--radius-s:9px;--pad:16px}
*{box-sizing:border-box}html,body{max-width:100%}
html{background:var(--bg)}
body{margin:0;padding:0 16px 48px;background:
radial-gradient(1200px 500px at 15% -10%,rgba(95,227,255,.10),transparent 60%),
radial-gradient(900px 400px at 100% 0%,rgba(201,255,79,.07),transparent 55%),var(--bg);
color:var(--fg);font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
overflow-wrap:anywhere;word-break:break-word;-webkit-font-smoothing:antialiased}
main{max-width:1180px;margin:0 auto}
[id]{scroll-margin-top:72px}
a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:28px;line-height:1.15;letter-spacing:-.015em;margin:14px 0 4px;font-weight:800}
h2{font-size:20px;line-height:1.2;letter-spacing:-.01em;margin:36px 0 10px;padding:10px 0 8px;
border-top:2px solid var(--lime);border-bottom:1px solid var(--line);font-weight:800}
h3{font-size:16px;margin:14px 0 6px;font-weight:700;letter-spacing:-.005em}
p{margin:6px 0}.sub{color:var(--muted)}.small{font-size:13px}.dim{color:var(--dim)}
b,strong{font-weight:700}
.num,td.num,.pts{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.info{color:var(--info)}.lime{color:var(--lime)}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);
border-radius:var(--radius);padding:14px var(--pad);margin:12px 0;box-shadow:var(--shadow)}
.card.quiet{background:var(--bg2);box-shadow:none}
.badge,.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11.5px;font-weight:700;
letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--line2);background:var(--chip);
color:var(--fg);white-space:nowrap;vertical-align:middle}
.badge.go,.pill.ok{color:var(--lime);border-color:rgba(201,255,79,.45)}
.badge.cond{color:var(--cyan);border-color:rgba(95,227,255,.45)}
.badge.held,.pill.warn{color:var(--warn);border-color:rgba(255,200,107,.45)}
.badge.now,.pill.bad{color:var(--bad);border-color:rgba(255,128,128,.5)}
.badge.today,.pill.info{color:var(--cyan);border-color:rgba(95,227,255,.4)}
.pill.solid{background:var(--lime);color:var(--lime-ink);border-color:transparent}
.banner{border-left:4px solid var(--bad);padding:10px 14px;margin:12px 0;background:var(--card);
border-radius:0 var(--radius-s) var(--radius-s) 0}
.banner.ok{border-left-color:var(--lime)}.banner.warn{border-left-color:var(--warn)}
.gatebox{border:1px dashed var(--warn);border-radius:var(--radius-s);padding:10px 12px;margin:10px 0;color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:8px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:700;color:var(--muted);white-space:nowrap;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase}
td.num{text-align:right;white-space:nowrap;font-weight:700}
tr:last-child td{border-bottom:0}
.wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:var(--radius-s)}
details{margin:6px 0}summary{cursor:pointer;color:var(--cyan);padding:4px 0;font-weight:600}
summary:hover{text-decoration:underline}
code,pre{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere;word-break:break-all}
pre{background:var(--bg2);border:1px solid var(--line);padding:10px;border-radius:var(--radius-s);overflow-x:auto;white-space:pre-wrap}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
button,.btn{font:inherit;font-weight:700;padding:9px 16px;border-radius:999px;border:1px solid var(--line2);
background:var(--chip);color:var(--fg);cursor:pointer;min-height:42px;letter-spacing:.01em}
button.primary{background:var(--lime);color:var(--lime-ink);border-color:transparent}
button[disabled]{opacity:.55;cursor:default}
button:hover:not([disabled]){border-color:var(--cyan)}
input[type=search],select{font:inherit;padding:9px 12px;border-radius:var(--radius-s);border:1px solid var(--line2);
background:var(--bg2);color:var(--fg);min-height:42px}
input[type=search]{width:100%}
:is(button,summary,a,input,select,[tabindex]):focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:6px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}
.kpi>div{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius-s);padding:10px 12px;font-size:12px;
color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.kpi b{display:block;font-size:24px;color:var(--fg);letter-spacing:-.01em;text-transform:none;font-variant-numeric:tabular-nums;margin-top:2px}
.act{border:1px solid var(--line);border-left:4px solid var(--lime);border-radius:var(--radius);padding:12px 14px;margin:10px 0;
background:linear-gradient(180deg,var(--card2),var(--card));box-shadow:var(--shadow)}
.act.withheld,.act.off{border-left-color:var(--line2);border-left-style:dashed;background:var(--bg2);box-shadow:none}
.act.conditional{border-left-color:var(--cyan)}
.act.act-NOW{border-left-color:var(--bad)}.act.act-NOW.withheld{border-left-color:var(--line2)}
.act h3{margin:6px 0 4px;font-size:17px}.act .bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.act .why{font-size:13px;color:var(--muted)}.act .deadline{font-weight:700}.act .backup{color:var(--muted);font-size:13.5px}
.nav{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:center;gap:6px;margin:0 -16px 8px;padding:8px 16px;
background:rgba(11,16,32,.86);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.nav a{flex:1 1 0;max-width:220px;text-align:center;padding:10px 6px;min-height:44px;border-radius:999px;color:var(--muted);font-weight:700;
font-size:13.5px;letter-spacing:.02em;border:1px solid transparent}
.nav a:hover{text-decoration:none;color:var(--fg);border-color:var(--line2)}
.nav a[aria-current=page]{color:var(--lime-ink);background:var(--lime)}
.nav .brand{flex:0 0 auto;font-weight:900;letter-spacing:.12em;text-transform:uppercase;font-size:12px;color:var(--cyan);padding:0 6px 0 0}
.ages{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 4px;font-size:12.5px;color:var(--muted)}
.ages span b{color:var(--fg);font-weight:600}
.snap{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0;font-size:13px}
.snap .status{flex:1 1 220px;min-height:1.4em;color:var(--muted)}
.snap button{min-height:36px;padding:6px 12px;font-size:13px}
.snapbar{display:none;align-items:center;gap:10px;flex-wrap:wrap;border:1px solid rgba(201,255,79,.5);background:rgba(201,255,79,.08);
border-radius:var(--radius-s);padding:10px 12px;margin:8px 0}
.snapbar.show{display:flex}
.hidden{display:none}
.vstate{display:block;margin-top:3px;font-size:12.5px;font-weight:700;color:var(--warn);letter-spacing:.01em}
.vstate:empty{display:none}
li[data-live] .badge,[data-live] .badge.v-LINEUP,[data-held] .badge.v-LINEUP{background:transparent;color:var(--muted);border-color:var(--line2);text-decoration:line-through}
[data-live] .stat.plus,[data-held] .stat.plus{color:var(--muted)}
.act[data-live]{border-left-color:var(--line2);border-left-style:dashed;background:var(--bg2);box-shadow:none}
.act[data-live] h3{color:var(--muted)}.act[data-live] .bar .badge{opacity:.6;text-decoration:line-through}
#validity{border-left-color:var(--warn)}.banner[data-live]{border-left-color:var(--warn)}.banner[data-live] b.ok{color:var(--muted);text-decoration:line-through}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chips button{min-height:36px;padding:6px 12px;font-size:13px}
.chips button[aria-pressed=true]{background:var(--cyan);color:var(--lime-ink);border-color:transparent}
.controls{display:grid;grid-template-columns:1fr;gap:8px;margin:8px 0}
.controls label{font-size:12px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;display:block;margin-bottom:4px}
.eyebrow{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);font-weight:700}
.stat{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.stat.plus{color:var(--lime)}.kpi b.lime{color:var(--lime)}.stat.minus{color:var(--muted)}
@media (min-width:700px){.controls{grid-template-columns:2fr 1fr 1fr}body{padding:0 24px 64px}h1{font-size:34px}}
@media (min-width:1100px){body{padding:0 32px 80px}}
@media (max-width:560px){h1{font-size:24px}.card{padding:12px 13px}.act{padding:11px 12px}.nav a{font-size:12.5px;padding:10px 2px}
.nav .brand{display:none}}
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
    """The snapshot-check strip: a status line, Check now, Pause, and the
    hidden banner that appears when a newer build is published."""
    return (
        "<div class=\"snap\" id=\"snap\">"
        "<span class=\"status\" id=\"snap-status\" role=\"status\" aria-live=\"polite\">"
        "Published-build check: needs the hosted site and its script.</span>"
        "<button type=\"button\" id=\"snap-check\" disabled>Check now</button>"
        "<button type=\"button\" id=\"snap-pause\" aria-pressed=\"false\" disabled>Pause</button>"
        "</div>"
        "<div class=\"snapbar\" id=\"snap-banner\" role=\"status\" aria-live=\"polite\">"
        "<span id=\"snap-banner-text\"></span>"
        "<button type=\"button\" class=\"primary\" id=\"snap-reload\">Reload this page</button>"
        "</div>")


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
tick(); if(typeof setInterval==='function') setInterval(tick,30000);
window.gridironAges={tick:tick};
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
