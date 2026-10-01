"""Package the two rendered pages for GitHub Pages, and check the package.

    PYTHONPATH=src python scripts/cloud/pages_site.py build --out .site
    PYTHONPATH=src python scripts/cloud/pages_site.py check --site .site --width 375

`build` copies `gameday_latest.html` and `dashboard_latest.html` from the
render directory into `--out`, byte for byte, writes `index.html` (a redirect
to Game Day), and exits 1 WITHOUT writing anything when either page is
missing or when any page carries a forbidden string. The allowlist, the scan
and the reasons live in `gridiron.publication`; this file is the command
line around it. In the workflow a non-zero exit here means no Pages artifact
and no deploy, so the last good site stays up.

`check` serves the built directory under a `/gridiron/` prefix — the same
project path the public site has — on a loopback port, opens the root in a
headless browser at the given width and reports what a phone would find:
where the root lands, whether each page's link to the other resolves,
whether the page fits the width without sideways scroll, and whether the
Game Day refresh button comes alive and survives one tap. It never asserts
what the tap fetched: a fixture is the place to script responses (the drive
in scripts/weekly/gameday_scenarios.py does), and from here the page talks
to whatever host it was rendered for. When no browser is present the check
says UNAVAILABLE; it does not pass.

Nothing printed here names a player: filenames, sizes, link targets and
element states only.
"""
from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from gridiron import publication
from gridiron.paths import OUTPUTS, REPO_ROOT

#: Where the two renderers write (scripts/weekly/dashboard.py and gameday.py).
DASHBOARD_DIR = OUTPUTS / "dashboard"

PREFIX = "/gridiron/"

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


# The harness is served from the same origin as the site so it may read into
# the frame. It holds the document's load event open with an image the server
# answers only after the harness has asked for /__done, which is what lets a
# `--dump-dom` run wait for real fetches and timers instead of virtual time.
_HARNESS = r"""<!doctype html><html><head><meta charset="utf-8"><title>check</title></head>
<body style="margin:0"><iframe id="f" style="width:__WIDTH__px;height:900px;border:0"></iframe>
<img id="hold" src="/__wait" alt="">
<script>
(function(){
  var R = {width: __WIDTH__, steps: []};
  var f = document.getElementById('f');
  function done(){ document.title = 'RESULT ' + btoa(unescape(encodeURIComponent(JSON.stringify(R))));
    fetch('/__done').catch(function(){}); }
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function loaded(){ return new Promise(function(r){ f.addEventListener('load', function h(){ f.removeEventListener('load', h); r(); }); }); }
  function fit(doc){ var de = doc.documentElement; return {viewport: f.clientWidth, clientWidth: de.clientWidth,
    scrollWidth: de.scrollWidth, overflow: de.scrollWidth - de.clientWidth}; }
  function links(doc){ var out = []; var as = doc.querySelectorAll('a[href]');
    for (var i = 0; i < as.length; i++) out.push(as[i].getAttribute('href')); return out; }
  (async function(){
    try {
      var p = loaded(); f.src = '__PREFIX__'; await p;
      // the meta refresh navigates the frame once more
      var t0 = Date.now();
      while (Date.now() - t0 < 4000 && !/gameday_latest\.html$/.test(f.contentWindow.location.pathname)) await wait(50);
      var doc = f.contentDocument;
      R.steps.push({page: 'root', landed: f.contentWindow.location.pathname, title: doc.title,
        fit: fit(doc), links: links(doc)});
      var api = f.contentWindow.gridironGameDay;
      var btn = doc.getElementById('gd-refresh');
      R.gameday = {script: !!api, buttonDisabledAtLoad: btn ? btn.disabled : null,
        modeAtLoad: (doc.getElementById('gd-modepill')||{}).textContent || null};
      if (btn && !btn.disabled) {
        btn.click();
        var t1 = Date.now();
        while (Date.now() - t1 < 20000 && btn.disabled) await wait(100);
        R.gameday.tap = {buttonDisabledAfter: btn.disabled, waitedMs: Date.now() - t1,
          mode: (doc.getElementById('gd-modepill')||{}).textContent || null,
          status: ((doc.getElementById('gd-status')||{}).textContent || '').slice(0, 160),
          hasState: !!(api && api.state && api.state().lastGood)};
      }
      // follow the page's own link to the board, then the board's link back:
      // the anchors are CLICKED so each relative href resolves against the
      // page that carries it, exactly as a reader's tap would.
      function anchor(d, re){ var as = d.querySelectorAll('a[href]');
        for (var i = 0; i < as.length; i++) if (re.test(as[i].getAttribute('href'))) return as[i]; return null; }
      var toBoard = anchor(doc, /dashboard_latest\.html$/);
      R.gameday.linkToBoard = toBoard ? toBoard.getAttribute('href') : null;
      if (toBoard) {
        p = loaded(); toBoard.click(); await p;
        doc = f.contentDocument;
        var back = anchor(doc, /gameday_latest\.html$/);
        R.steps.push({page: 'board', landed: f.contentWindow.location.pathname, title: doc.title,
          fit: fit(doc), linkToGameday: back ? back.getAttribute('href') : null});
        if (back) { p = loaded(); back.click(); await p;
          R.steps.push({page: 'gameday-again', landed: f.contentWindow.location.pathname,
            title: f.contentDocument.title}); }
      }
      var r = await fetch('__PREFIX__dashboard_latest.json'); R.jsonStatus = r.status;
      r = await fetch('__PREFIX__gameday_latest.json'); R.jsonStatus2 = r.status;
    } catch (e) { R.error = String(e); }
    done();
  })();
})();
</script></body></html>
"""


class _Site(http.server.BaseHTTPRequestHandler):
    root: Path = Path(".")
    harness: bytes = b""
    release = threading.Event()
    hits: list[str] = []

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        _Site.hits.append(path)
        if path == "/__harness":
            return self._send(200, _Site.harness, "text/html; charset=utf-8")
        if path == "/__wait":
            _Site.release.wait(60)
            return self._send(200, b"", "image/gif")
        if path == "/__done":
            _Site.release.set()
            return self._send(200, b"ok", "text/plain")
        if path == PREFIX.rstrip("/"):
            self.send_response(301)
            self.send_header("Location", PREFIX)
            self.end_headers()
            return None
        if path.startswith(PREFIX):
            rel = path[len(PREFIX):] or publication.INDEX_PAGE
            target = (_Site.root / rel)
            if "/" not in rel and target.is_file() and rel in publication.SITE_FILES:
                return self._send(200, target.read_bytes(), "text/html; charset=utf-8")
        return self._send(404, b"not found", "text/plain")


def check(site: Path, width: int) -> dict:
    problems = publication.verify_site(site)
    result: dict = {"site": str(site), "width": width, "static": problems}
    binary = chrome_binary()
    if binary is None:
        result["executed"] = False
        result["reason"] = "no headless browser on this machine"
        return result
    _Site.root = site
    _Site.release = threading.Event()
    _Site.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    port = server.server_address[1]
    _Site.harness = _HARNESS.replace("__WIDTH__", str(width)).replace("__PREFIX__", PREFIX).encode("utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             f"--window-size={max(width + 40, 500)},1000", "--dump-dom",
             f"http://127.0.0.1:{port}/__harness"],
            capture_output=True, timeout=120)
        dom = proc.stdout.decode("utf-8", "replace")
        start = dom.find("<title>RESULT ")
        if start < 0:
            result.update(executed=False, reason="harness produced no result",
                          hits=list(_Site.hits))
            return result
        token = dom[start + len("<title>RESULT "):dom.find("</title>", start)]
        out = json.loads(base64.b64decode(token).decode("utf-8"))
        result.update(executed=True, hits=list(_Site.hits), **out)
        return result
    finally:
        _Site.release.set()
        server.shutdown()
        server.server_close()


def verdict(result: dict) -> list[str]:
    """Plain failures in a check result, or [] when everything held."""
    bad = list(result.get("static") or [])
    if not result.get("executed"):
        return bad + [f"UNAVAILABLE: {result.get('reason', 'not executed')}"]
    steps = {s["page"]: s for s in result.get("steps", [])}
    root = steps.get("root")
    if not root or not root["landed"].endswith(publication.GAMEDAY_PAGE):
        bad.append(f"root did not land on Game Day: {root and root['landed']}")
    if root and root["fit"]["overflow"] > 0:
        bad.append(f"Game Day overflows by {root['fit']['overflow']}px at {result['width']}px")
    gd = result.get("gameday") or {}
    if not gd.get("script"):
        bad.append("Game Day script did not run under the site origin")
    if not gd.get("linkToBoard"):
        bad.append("Game Day has no link to the board")
    tap = gd.get("tap")
    if tap is None:
        bad.append("refresh button never became tappable")
    elif tap["buttonDisabledAfter"]:
        bad.append("refresh button stayed disabled after one tap")
    board = steps.get("board")
    if not board:
        bad.append("the board did not open from Game Day")
    else:
        if board["fit"]["overflow"] > 0:
            bad.append(f"board overflows by {board['fit']['overflow']}px at {result['width']}px")
        if not board.get("linkToGameday"):
            bad.append("the board has no link back to Game Day")
    if "gameday-again" not in steps:
        bad.append("the board's link back did not reopen Game Day")
    for key in ("jsonStatus", "jsonStatus2"):
        if result.get(key) == 200:
            bad.append("a JSON record is served beside the pages")
    if result.get("error"):
        bad.append(f"harness error: {result['error']}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="package the two pages into a site directory")
    b.add_argument("--source", type=Path, default=DASHBOARD_DIR,
                   help="the render directory (default data/outputs/dashboard/)")
    b.add_argument("--out", type=Path, default=REPO_ROOT / ".site")
    c = sub.add_parser("check", help="serve the site under /gridiron/ and probe it")
    c.add_argument("--site", type=Path, default=REPO_ROOT / ".site")
    c.add_argument("--width", type=int, default=375)
    c.add_argument("--json", type=Path, default=None, help="write the full result here")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        try:
            built = publication.build_site(args.source, args.out)
        except publication.PublicationError as e:
            print(f"[pages] REFUSED: {e}", file=sys.stderr)
            print("[pages] nothing written; the last good site stays up", file=sys.stderr)
            return 1
        for line in built.lines():
            print(f"[pages] {line}")
        print(f"[pages] site ready at {built.site}")
        return 0

    result = check(args.site, args.width)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    bad = verdict(result)
    for s in result.get("steps", []):
        print(f"[pages] {s['page']}: {s.get('landed')}" + (
            f"  viewport {s['fit']['viewport']}px, content {s['fit']['clientWidth']}px, "
            f"overflow {s['fit']['overflow']}px" if s.get("fit") else ""))
    gd = result.get("gameday") or {}
    if gd.get("tap"):
        t = gd["tap"]
        print(f"[pages] refresh tap: mode {t['mode']!r}, button re-enabled after {t['waitedMs']} ms")
    for line in bad:
        print(f"[pages] FAIL: {line}")
    print("[pages] " + ("PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
