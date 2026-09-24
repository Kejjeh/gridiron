"""Drive the board's own scripts against a loopback fixture that plays the
hosting server. OFFLINE apart from 127.0.0.1.

    PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --browser --only complete

Two things on the board page run in the browser and nowhere else: the Free
Agent Radar's filters, search, sort and disclosures, and the published-build
check that asks the hosting server whether a newer build of the page exists.
Neither can be shown to work by reading HTML, so this module serves the
rendered `dashboard_latest.html` under the site's own `/gridiron/` prefix
from a fixture whose behaviour is scripted step by step, opens it in a
375 px iframe with the pre-installed headless Chromium, and walks the page's
public hooks (`window.gridironRadar`, `window.gridironSnapshot`) in REAL
time, reading the DOM back after every step:

  same        the fixture serves the same build: a first check is a 200
              with the same stamp, the next a 304 against the ETag
  429         the fixture rate-limits: the check fails, backs off, and the
              page is unchanged and still usable
  drop        the fixture closes the socket without answering: a network
              error, a longer backoff
  recover     the fixture answers again: 304, failures reset
  inflight    two checks at once: the second is refused, not doubled
  wrong       the fixture serves a DIFFERENT page (newer stamp, other
              data-page) at this address: refused, no banner
  garbled     the fixture serves this page with an unreadable stamp: no
              banner, nothing assumed
  online      the device reports it is back online: one check is armed
              within seconds, not after the backoff
  new         the fixture serves a NEWER build: the banner appears, the
              checks pause, nothing on the page is swapped
  validity    the page's clock is moved past its evidence expiry, then past
              kickoff: moves are marked EXPIRED, then OFF/LOCKED in place,
              the banner says why, the LINEUP filter empties, and moving
              the clock back restores the page as built
  pause       Pause clears the timer; Resume re-arms exactly one
  radar       position chips, search, verdict filter, sort, a disclosure
              opened, the count line, keyboard focus on the controls, and
              an Action Desk link landing on a row the filters had hidden
  reload      filters chosen, the frame reloaded: the filters are back

The fixture counts every request, so a duplicate timer would show up as
more hits than checks. When no browser is present the drive reports
UNAVAILABLE; it never reports a pass it did not execute.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

PREFIX = "/gridiron/"
PAGE = "dashboard_latest.html"

_CHROME_CANDIDATES = (
    os.environ.get("GRIDIRON_CHROME", ""),
    "/opt/pw-browsers/chromium",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    shutil.which("google-chrome") or "", shutil.which("chrome") or "",
)


def chrome_binary() -> str | None:
    for c in _CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def _restamp(html: str, stamp: str) -> str:
    """The same page with a different build stamp: what a newer deploy
    looks like to the check (the meta is the only thing it reads)."""
    return re.sub(r'(name="gridiron-build" content=")([^"]+)(")', rf"\g<1>{stamp}\g<3>", html, count=1)


class _Fixture(http.server.BaseHTTPRequestHandler):
    """Serves the page under /gridiron/ with an ETag, honours If-None-Match,
    and behaves per the current step: same | 429 | drop | hang | new."""

    page: bytes = b""
    newer: bytes = b""
    state: dict = {"mode": "same"}
    hits: list = []
    wrong: bytes = b""
    garbled: bytes = b""
    harness: bytes = b""
    release = threading.Event()

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes | None, ctype: str = "text/html; charset=utf-8",
              etag: str | None = None) -> None:
        self.send_response(code)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-store")
        if body is not None:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        cls = type(self)
        if path.startswith("/__mode/"):
            cls.state["mode"] = path.rsplit("/", 1)[1]
            return self._send(200, b"{}", "application/json")
        if path == "/__harness":
            return self._send(200, cls.harness)
        if path == "/__wait":
            cls.release.wait(180)
            return self._send(200, b"", "image/gif")
        if path == "/__done":
            cls.release.set()
            return self._send(200, b"ok", "text/plain")
        if path == "/__hits":
            return self._send(200, json.dumps(cls.hits).encode(), "application/json")
        if path == PREFIX + PAGE:
            mode = cls.state.get("mode", "same")
            cls.hits.append(mode)
            if mode == "429":
                return self._send(429, b"slow down", "text/plain")
            if mode == "drop":
                try:
                    self.connection.shutdown(2)
                except OSError:
                    pass
                self.close_connection = True
                return None
            if mode == "hang":
                cls.release.wait(4)          # longer than the page's timeout
                return self._send(200, cls.page)
            body = {"new": cls.newer, "wrong": cls.wrong, "garbled": cls.garbled}.get(mode, cls.page)
            etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, None, etag=etag)
            return self._send(200, body, etag=etag)
        return self._send(404, b"not found", "text/plain")


_HARNESS = r"""<!doctype html><html><head><meta charset="utf-8"><title>drive</title></head>
<body style="margin:0"><iframe id="f" style="width:375px;height:1400px;border:0"></iframe>
<img id="hold" src="/__wait" alt="">
<script>
(function(){
  var R = {steps: []};
  var f = document.getElementById('f');
  function done(){ document.title = 'RESULT ' + btoa(unescape(encodeURIComponent(JSON.stringify(R)))); fetch('/__done').catch(function(){}); }
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function loaded(){ return new Promise(function(r){ f.addEventListener('load', function h(){ f.removeEventListener('load', h); r(); }); }); }
  function txt(d, sel){ var e = d.querySelector(sel); return e ? e.textContent : null; }
  function snapState(label, extra){
    var w = f.contentWindow, d = f.contentDocument, api = w.gridironSnapshot, st = api ? api.state() : null;
    var rec = {label: label, status: txt(d, '#snap-status'), bannerShown: /\bshow\b/.test((d.getElementById('snap-banner')||{}).className || ''),
      bannerText: txt(d, '#snap-banner-text'), failures: st ? st.failures : null, checks: st ? st.checks : null, timer: st ? (st.timer !== null) : null,
      nextMs: st ? st.nextMs : null, paused: st ? st.paused : null, newer: st ? st.newer : null, inflight: st ? st.inflight : null,
      lastResult: st ? st.lastResult : null, pauseLabel: txt(d, '#snap-pause'), checkDisabled: (d.getElementById('snap-check')||{}).disabled};
    if (extra) for (var k in extra) rec[k] = extra[k];
    R.steps.push(rec); return rec; }
  async function mode(m){ await fetch('/__mode/' + m); }
  function visibleRows(d){ return d.querySelectorAll('#radar-list li.rrow:not([hidden])').length; }
  function firstName(d){ var e = d.querySelector('#radar-list li.rrow:not([hidden]) .rname'); return e ? e.textContent : null; }
  (async function(){
    try {
      var p = loaded(); f.src = '__PREFIX__' + '__PAGE__'; await p;
      var w = f.contentWindow, d = f.contentDocument, api = w.gridironSnapshot, radar = w.gridironRadar;
      R.own = (d.querySelector('meta[name="gridiron-build"]')||{}).getAttribute ? d.querySelector('meta[name="gridiron-build"]').getAttribute('content') : null;
      R.fit = {clientWidth: d.documentElement.clientWidth, scrollWidth: d.documentElement.scrollWidth, overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth};
      R.nav = Array.prototype.map.call(d.querySelectorAll('nav.nav a'), function(a){ return [a.textContent, a.getAttribute('href'), a.getAttribute('aria-current')]; });
      if (!api || !radar || !w.gridironValidity) throw new Error('page scripts did not expose gridironSnapshot/gridironRadar/gridironValidity');
      // The page is judged at its build instant, whatever today's date is:
      // a drive run after the fixture's kickoff must not see it lapsed.
      var built = Date.parse(R.own); w.gridironValidity.state().skewMs = built - Date.now(); w.gridironValidity.tick(built);
      api.tune({intervalMs: 600000, throttleMs: 0, timeoutMs: 1500, backoffMaxMs: 900000});
      snapState('initial');
      await mode('same'); snapState('same-first', {result: await api.check(true)});
      snapState('same-304', {result: await api.check(true)});
      await mode('429'); snapState('rate-limited', {result: await api.check(true)});
      await mode('drop'); snapState('dropped', {result: await api.check(true)});
      await mode('hang'); snapState('hung', {result: await api.check(true)});
      await mode('same'); snapState('recovered', {result: await api.check(true)});
      var p1 = api.check(true), p2 = api.check(true); var r2 = await p2; var r1 = await p1;
      snapState('inflight', {result: r1, second: r2});
      // pause / resume: the timer is cleared, then re-armed exactly once
      d.getElementById('snap-pause').click(); snapState('paused');
      d.getElementById('snap-pause').click(); await wait(1200); snapState('resumed');
      await mode('wrong'); snapState('wrong-page', {result: await api.check(true)});
      await mode('garbled'); snapState('garbled', {result: await api.check(true)});
      await mode('same'); api.tune({intervalMs: 600000}); w.dispatchEvent(new Event('online')); snapState('online');
      await wait(1600); snapState('online-checked');
      api.tune({intervalMs: 600000});
      await mode('new'); snapState('newer', {result: await api.check(true)});
      // the page itself did not change under the reader
      R.stillOwn = (d.querySelector('meta[name="gridiron-build"]').getAttribute('content') === R.own);
      R.radar = {};
      R.radar.total = radar.total;
      R.radar.all = visibleRows(d);
      R.radar.qb = radar.set({pos: 'QB'});
      R.radar.qbVisible = visibleRows(d);
      R.radar.qbPressed = Array.prototype.map.call(d.querySelectorAll('#radar-pos button'), function(b){ return b.textContent + ':' + b.getAttribute('aria-pressed'); });
      R.radar.search = radar.set({pos: '', q: 'coker'});
      R.radar.searchName = firstName(d);
      R.radar.lineup = radar.set({q: '', verdict: 'LINEUP'});
      R.radar.lineupNames = Array.prototype.map.call(d.querySelectorAll('#radar-list li.rrow:not([hidden]) .rname'), function(e){ return e.textContent; });
      R.radar.byProj = radar.set({verdict: '', sort: 'proj'});
      R.radar.byProjFirst = firstName(d);
      R.radar.byProjOrder = Array.prototype.map.call(d.querySelectorAll('#radar-list li.rrow:not([hidden])'), function(e){ return parseFloat(e.getAttribute('data-proj')); });
      R.radar.none = radar.set({q: 'zzzz-no-such-player'});
      R.radar.emptyShown = !d.getElementById('radar-empty').hasAttribute('hidden');
      R.radar.countText = txt(d, '#radar-count');
      radar.set({q: '', sort: 'verdict'});
      var det = d.querySelector('#radar-list li.rrow details'); det.open = true;
      R.radar.expanded = det.open; R.radar.bodyText = (det.querySelector('.rbody')||{}).textContent || '';
      R.radar.bodyHasDrop = /Cost/.test(R.radar.bodyText) && /Alternatives/.test(R.radar.bodyText) && /UNVERIFIED/.test(R.radar.bodyText);
      // keyboard: the controls are native and focusable (the build check
      // lives in the Data & freshness disclosure, opened the way a reader would)
      var fresh = d.getElementById('data-freshness'); if (fresh) fresh.open = true;
      var focusables = [d.getElementById('radar-q'), d.getElementById('radar-v'), d.getElementById('radar-s'),
                        d.querySelector('#radar-pos button'), det.querySelector('summary'), d.getElementById('snap-check'),
                        d.querySelector('#desk a.btn'), d.querySelector('#desk .full summary')];
      R.radar.focusable = focusables.map(function(el){ if (!el) return null; el.focus(); return d.activeElement === el; });
      R.radar.outline = d.defaultView.getComputedStyle(d.getElementById('radar-q')).outlineStyle;
      // deep link from the Action Desk: a row the saved filters hide is
      // revealed (filters cleared), opened and focused
      var deskLink = d.querySelector('#desk a.btn[href^="#fa-"]');
      if (deskLink) { var tid = deskLink.getAttribute('href').slice(1);
        radar.set({q: 'zzzz-no-such-player'});
        var hiddenBefore = d.getElementById(tid).hasAttribute('hidden');
        deskLink.click(); await wait(400);
        var trow = d.getElementById(tid);
        R.radar.deepLink = {id: tid, hiddenBefore: hiddenBefore, visible: !trow.hasAttribute('hidden'),
          open: trow.querySelector('details').open, q: radar.state().q,
          focused: d.activeElement === trow.querySelector('summary')};
        radar.set({q: ''}); }
      // reload keeps the filters (the snapshot banner's reload path)
      radar.set({pos: 'WR', sort: 'name', verdict: ''});
      await mode('same');
      p = loaded(); d.getElementById('snap-reload').click(); await p;
      w = f.contentWindow; d = f.contentDocument; radar = w.gridironRadar;
      if (w.gridironValidity) { w.gridironValidity.state().skewMs = built - Date.now(); w.gridironValidity.tick(built); }
      R.afterReload = {state: w.gridironRadar ? w.gridironRadar.state() : null, visible: visibleRows(d),
        own: (d.querySelector('meta[name="gridiron-build"]')||{getAttribute:function(){return null;}}).getAttribute('content'),
        status: txt(d, '#snap-status')};
      // validity: evidence expiry, then kickoff, then back to the build instant
      var V = w.gridironValidity, meta = d.querySelector('meta[name="gridiron-valid-until"]');
      var until = meta ? Date.parse(meta.getAttribute('content')) : NaN;
      var dls = Array.prototype.map.call(d.querySelectorAll('li.rrow[data-verdict-built="LINEUP"]'), function(e){ return Date.parse(e.getAttribute('data-deadline')); });
      var kick = dls.length ? Math.min.apply(null, dls) : NaN;
      function vstate(label, t){ var res = V.tick(t);
        var lin = d.querySelectorAll('li.rrow[data-verdict-built="LINEUP"]');
        return {label: label, expired: res.expired, lapsed: res.lapsed, banner: !d.getElementById('validity').hidden,
          head: txt(d, '#validity-head'), text: txt(d, '#validity-text'),
          rowsLive: Array.prototype.map.call(lin, function(e){ return e.getAttribute('data-live'); }),
          rowsVerdict: Array.prototype.map.call(lin, function(e){ return e.getAttribute('data-verdict'); }),
          rowNote: lin.length ? txt(lin[0], '.vstate') : null,
          cardsLive: Array.prototype.map.call(d.querySelectorAll('.dcard[data-deadline]'), function(e){ return e.getAttribute('data-live'); }),
          cardNote: (function(){ var c = d.querySelector('.dcard[data-deadline] .vstate'); return c ? c.textContent : null; })(),
          kpi: txt(d, '[data-live-count="LINEUP"]'), lineupFilter: radar.set({verdict: 'LINEUP'})}; }
      R.validity = {until: until, kick: kick, steps: [vstate('built', built), vstate('expired', until + 60000),
        vstate('kickoff', kick + 60000), vstate('back', built)]};
      radar.set({verdict: ''});
      R.hits = await (await fetch('/__hits')).json();
    } catch (e) { R.error = String(e && e.stack || e); }
    done();
  })();
})();
</script></body></html>
"""


def drive_radar(page: Path) -> dict:
    """Serve `page` from the fixture and drive it. Returns the full result,
    with `executed` False and a reason when no browser is present."""
    binary = chrome_binary()
    result: dict = {"page": str(page), "executed": False}
    if binary is None:
        result["reason"] = "no headless browser on this machine"
        return result
    html = page.read_text("utf-8")
    m = re.search(r'name="gridiron-build" content="([^"]+)"', html)
    if not m:
        result["reason"] = "the page carries no build stamp"
        return result
    own = m.group(1)
    _Fixture.page = html.encode("utf-8")
    # A stamp that sorts after the page's own: the same date at the end of
    # the day is enough for a string comparison of ISO-8601 UTC stamps.
    _Fixture.newer = _restamp(html, own[:11] + "23:59:59+00:00").encode("utf-8")
    _Fixture.wrong = _restamp(html, own[:11] + "23:59:59+00:00").replace(
        'data-page="dashboard_latest.html"', 'data-page="gameday_latest.html"', 1).encode("utf-8")
    _Fixture.garbled = _restamp(html, "zzzz-not-a-stamp").encode("utf-8")
    _Fixture.state = {"mode": "same"}
    _Fixture.hits = []
    _Fixture.release = threading.Event()
    _Fixture.harness = _HARNESS.replace("__PREFIX__", PREFIX).replace("__PAGE__", PAGE).encode("utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fixture)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--window-size=500,1500", "--dump-dom", f"http://127.0.0.1:{port}/__harness"],
            capture_output=True, timeout=240)
        dom = proc.stdout.decode("utf-8", "replace")
        start = dom.find("<title>RESULT ")
        if start < 0:
            result["reason"] = "harness produced no result"
            result["hits"] = list(_Fixture.hits)
            return result
        token = dom[start + len("<title>RESULT "):dom.find("</title>", start)]
        out = json.loads(base64.b64decode(token).decode("utf-8"))
        out.pop("own", None)
        result.update(executed=True, own=own, **out)
        result["lines"] = _lines(result)
        return result
    finally:
        _Fixture.release.set()
        server.shutdown()
        server.server_close()


def _step(result: dict, label: str) -> dict:
    return next((s for s in result.get("steps", []) if s.get("label") == label), {})


def _lines(result: dict) -> list[str]:
    out = []
    for s in result.get("steps", []):
        out.append(f"{s['label']}: result={s.get('result')!r} failures={s.get('failures')} "
                   f"timer={s.get('timer')} next={s.get('nextMs')} banner={s.get('bannerShown')}")
    r = result.get("radar") or {}
    out.append(f"radar: total={r.get('total')} qb={r.get('qb')} search={r.get('search')} "
               f"lineup={r.get('lineup')} none={r.get('none')} expanded={r.get('expanded')} "
               f"focusable={r.get('focusable')}")
    a = result.get("afterReload") or {}
    out.append(f"after reload: state={a.get('state')} visible={a.get('visible')}")
    for s in (result.get("validity") or {}).get("steps", []):
        out.append(f"validity {s['label']}: expired={s.get('expired')} lapsed={s.get('lapsed')} "
                   f"banner={s.get('banner')} rows={s.get('rowsLive')} verdicts={s.get('rowsVerdict')} "
                   f"cards={s.get('cardsLive')} kpi={s.get('kpi')} lineupFilter={s.get('lineupFilter')}")
    out.append(f"hits: {result.get('hits')}")
    return out


def verdict(result: dict) -> list[str]:
    """Plain failures, or [] when everything held. Every expectation here is
    a property of the page's script, not of the fixture's timing."""
    if not result.get("executed"):
        return [f"UNAVAILABLE: {result.get('reason', 'not executed')}"]
    bad: list[str] = []
    if result.get("error"):
        bad.append(f"harness error: {result['error']}")
    fit = result.get("fit") or {}
    if fit.get("overflow", 1) > 0 or fit.get("clientWidth", 0) > 375:
        bad.append(f"the board overflows at 375px: {fit}")
    nav = result.get("nav") or []
    if [n[0] for n in nav] != ["Game Day", "Board", "Free Agents"] or nav[1][2] != "page":
        bad.append(f"navigation is not the three pages with Board current: {nav}")
    init = _step(result, "initial")
    if not init.get("timer") or init.get("nextMs") != 600000:
        bad.append(f"no single timer armed after load: {init}")
    if _step(result, "same-first").get("result") != "same":
        bad.append(f"first check against the same build: {_step(result, 'same-first').get('result')}")
    if _step(result, "same-304").get("result") != "unchanged":
        bad.append("second check did not use the ETag (expected a 304 → unchanged)")
    rl = _step(result, "rate-limited")
    if rl.get("result") != "failed" or rl.get("failures") != 1 or "slower pace" not in (rl.get("status") or ""):
        bad.append(f"429 not reported as a backoff: {rl}")
    dr = _step(result, "dropped")
    if dr.get("result") != "failed" or dr.get("failures") != 2 or not (dr.get("nextMs") or 0) > (rl.get("nextMs") or 0):
        bad.append(f"a dropped connection did not back off further: {dr}")
    hg = _step(result, "hung")
    if hg.get("result") != "failed" or "timeout" not in (hg.get("status") or ""):
        bad.append(f"a hung request did not time out: {hg}")
    rc = _step(result, "recovered")
    if rc.get("result") != "unchanged" or rc.get("failures") != 0:
        bad.append(f"recovery did not reset the backoff: {rc}")
    inf = _step(result, "inflight")
    if inf.get("second") != "inflight":
        bad.append(f"a second concurrent check was not refused: {inf}")
    if _step(result, "paused").get("timer") is not False or _step(result, "paused").get("paused") is not True:
        bad.append("Pause did not clear the timer")
    res = _step(result, "resumed")
    if res.get("timer") is not True or res.get("paused") is not False:
        bad.append(f"Resume did not re-arm the timer: {res}")
    wp = _step(result, "wrong-page")
    if wp.get("result") != "wrongpage" or wp.get("bannerShown") or "wrong page" not in (wp.get("status") or ""):
        bad.append(f"a different page at this address was not refused: {wp}")
    gb = _step(result, "garbled")
    if gb.get("result") != "nostamp" or gb.get("bannerShown"):
        bad.append(f"an unreadable stamp was not refused: {gb}")
    on = _step(result, "online")
    if on.get("nextMs") != 1000 or "Back online" not in (on.get("status") or ""):
        bad.append(f"coming back online did not arm a prompt check: {on}")
    onc = _step(result, "online-checked")
    if onc.get("lastResult") != "unchanged" or onc.get("checks") != (on.get("checks") or 0) + 1:
        bad.append(f"the online check did not run exactly once: {onc}")
    nw = _step(result, "newer")
    if nw.get("result") != "newer" or not nw.get("bannerShown") or nw.get("timer") is not False:
        bad.append(f"a newer build did not raise the banner and pause the checks: {nw}")
    if not result.get("stillOwn"):
        bad.append("the page's own content changed under the reader")
    for s in result.get("steps", []):
        if s.get("bannerShown") and s["label"] not in ("newer",):
            bad.append(f"false 'newer build' banner at step {s['label']}")
    r = result.get("radar") or {}
    if not r or r.get("total", 0) < 1:
        bad.append("the radar listed no candidate")
    else:
        if r.get("all") != r.get("total"):
            bad.append("not every row is shown with no filter")
        if not (0 < (r.get("qb") or 0) < r["total"]) or r.get("qb") != r.get("qbVisible"):
            bad.append(f"the QB chip did not filter: {r.get('qb')} of {r['total']}")
        if r.get("search") != 1 or "Coker" not in (r.get("searchName") or ""):
            bad.append(f"search did not find the one WR: {r.get('search')} {r.get('searchName')}")
        if (r.get("lineup") or 0) < 1 or (r.get("lineup") or 0) >= r["total"]:
            bad.append(f"the LINEUP filter did not narrow: {r.get('lineup')}")
        order = r.get("byProjOrder") or []
        if order != sorted(order, reverse=True):
            bad.append(f"sort by projection is not descending: {order}")
        if r.get("none") != 0 or not r.get("emptyShown"):
            bad.append("an empty result did not show the empty line")
        if not r.get("expanded") or not r.get("bodyHasDrop"):
            bad.append("the first row's disclosure did not open on a full comparison")
        if not all(r.get("focusable") or []):
            bad.append(f"a control is not keyboard-focusable: {r.get('focusable')}")
        dl = r.get("deepLink") or {}
        if not (dl.get("hiddenBefore") and dl.get("visible") and dl.get("open")
                and dl.get("q") == "" and dl.get("focused")):
            bad.append(f"the Action Desk link did not reveal and open its radar row: {dl}")
    v = result.get("validity") or {}
    vs = {s["label"]: s for s in v.get("steps", [])}
    if not v or not (v.get("until") and v.get("kick")) or v["until"] >= v["kick"]:
        bad.append(f"the page carries no evidence expiry before its kickoff: {v.get('until')} {v.get('kick')}")
    else:
        b0, ex, ko, back = (vs.get(k, {}) for k in ("built", "expired", "kickoff", "back"))
        if b0.get("banner") or any(b0.get("rowsLive") or []) or (b0.get("lineupFilter") or 0) < 1:
            bad.append(f"at the build instant the page is not as built: {b0}")
        if not ex.get("expired") or not ex.get("banner") or set(ex.get("rowsLive") or []) != {"expired"} \
                or "EXPIRED" not in (ex.get("rowNote") or "") or "Evidence expired" not in (ex.get("head") or "") \
                or set(ex.get("cardsLive") or []) != {"expired"}:
            bad.append(f"past the evidence expiry the moves still read as current: {ex}")
        if set(ko.get("rowsLive") or []) != {"lapsed"} or set(ko.get("rowsVerdict") or []) != {"LOCKED"} \
                or "OFF" not in (ko.get("rowNote") or "") or ko.get("lineupFilter") != 0 \
                or ko.get("kpi") != "0" or set(ko.get("cardsLive") or []) != {"lapsed"} \
                or "OFF" not in (ko.get("cardNote") or ""):
            bad.append(f"past kickoff the moves were not withdrawn in place: {ko}")
        if back.get("banner") or any(back.get("rowsLive") or []) or back.get("kpi") != b0.get("kpi"):
            bad.append(f"moving the clock back did not restore the page: {back}")
    a = result.get("afterReload") or {}
    st = a.get("state") or {}
    if st.get("pos") != "WR" or st.get("sort") != "name":
        bad.append(f"the filters did not survive the reload: {st}")
    hits = result.get("hits") or []
    # one load, one reload, exactly one request per executed check (the
    # refused in-flight check makes none) and the two automatic checks that
    # Resume and the online event re-arm: a duplicate timer would add hits
    checks = [s for s in result.get("steps", []) if "result" in s and s.get("result") != "inflight"]
    expected = 2 + len(checks) + 2
    if len(hits) != expected:
        bad.append(f"{len(hits)} requests for {expected} expected (duplicate timer?): {hits}")
    return bad
