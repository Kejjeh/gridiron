"""Publication policy: what reaches GitHub Pages, and under which gates.

The owner authorised publishing the repository and the personalised pages
(2026-09-21). These tests hold the boundary of that authorisation:

  * the workflows run on a public repository only under an explicit opt-in
    variable, and the private-repository path is unchanged;
  * the Pages deploy is a separate job holding the only Pages permissions,
    SHA-pinned, on the schedule or a manual dispatch only, never on a pull
    request, and only after a package step that succeeded;
  * the package is exactly two allowlisted pages copied byte for byte plus an
    index that opens Game Day — no JSON record, no cache, no archive, no
    credential-shaped string — and a missing page refuses the whole package
    with nothing written, so the last good site stays up.

Workflow properties are checked on the files' TEXT, as tests/test_workflow_inputs.py
does: pyyaml is not a dependency of this project.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from gridiron import publication as pub

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
DASH = (WF / "dashboard-artifact.yml").read_text("utf-8")
SYNC = (WF / "sleeper-sync.yml").read_text("utf-8")
SHA_PIN = re.compile(r"^\s*uses:\s*\S+@([0-9a-f]{40})\s*(#.*)?$")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("pub_gameday_scenarios", "scripts/weekly/gameday_scenarios.py")
SITE = _load("pub_pages_site", "scripts/cloud/pages_site.py")


def _section(text: str, start: str, end: str | None = None) -> str:
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return text[i:j]


def _on_block(text: str) -> str:
    return _section(text, "\non:\n", "\npermissions:")


# ----------------------------------------------------------- the gates

@pytest.mark.parametrize("text, name", [(DASH, "dashboard"), (SYNC, "sync")])
def test_a_public_repository_builds_only_under_the_explicit_opt_in(text, name):
    """Private repositories keep the old gate; public ones need the second
    variable set to exactly `true`. Flipping visibility alone stops both
    workflows, which is the designed fail-safe."""
    gate = _section(text, "    if: >-", "    runs-on:")
    assert "vars.GRIDIRON_CLOUD_SYNC_ENABLED == 'true'" in gate, name
    assert "github.event.repository.private == true" in gate, name
    assert "vars.GRIDIRON_PUBLIC_PUBLICATION == 'true'" in gate, name
    # the private clause and the opt-in are alternatives, both under the sync flag
    assert re.search(r"\(github\.event\.repository\.private == true \|\| "
                     r"vars\.GRIDIRON_PUBLIC_PUBLICATION == 'true'\)", gate), name
    assert "&&" in gate.split("(")[0], f"{name}: the sync flag must still be required"


@pytest.mark.parametrize("text, name", [(DASH, "dashboard"), (SYNC, "sync")])
def test_the_only_triggers_are_the_schedule_and_a_manual_dispatch(text, name):
    on = _on_block(text)
    assert "workflow_dispatch:" in on and "schedule:" in on, name
    for forbidden in ("pull_request", "push:", "issue", "fork", "workflow_run", "repository_dispatch"):
        assert forbidden not in on, f"{name}: {forbidden} must never trigger a run"


def test_the_deploy_is_a_separate_job_holding_the_only_pages_permissions():
    top = _section(DASH, "\npermissions:", "\nconcurrency:")
    assert top.strip() == "permissions:\n  contents: read", "workflow-level token is read-only"
    render = _section(DASH, "\n  render:", "\n  publish:")
    publish = _section(DASH, "\n  publish:")
    assert "permissions:" not in render, "the render job must not widen the token"
    def granted(text: str, perm: str) -> int:   # YAML lines, not comments
        return sum(1 for l in text.splitlines() if l.strip() == perm)
    for perm in ("pages: write", "id-token: write"):
        assert granted(DASH, perm) == 1 and granted(publish, perm) == 1, perm
    assert "contents: write" not in DASH
    assert "needs: render" in publish
    assert "environment:\n      name: github-pages" in publish
    assert "timeout-minutes:" in publish
    assert "concurrency:\n      group: gridiron-pages" in publish
    assert "actions/checkout" not in publish, "the deploy job runs no repository code"


def test_the_deploy_runs_only_after_a_successful_package_under_the_opt_in():
    publish = _section(DASH, "\n  publish:")
    cond = _section(publish, "    if: >-", "    runs-on:")
    assert "needs.render.result == 'success'" in cond
    assert "needs.render.outputs.site == 'success'" in cond
    assert "vars.GRIDIRON_PUBLIC_PUBLICATION == 'true'" in cond
    assert "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')" in cond
    assert "outputs:\n      site: ${{ steps.site.outcome }}" in DASH
    render = _section(DASH, "\n  render:", "\n  publish:")
    site = _section(render, "      - name: Package the public site", "      - name: Upload the Pages artifact")
    assert "id: site" in site
    assert "if: vars.GRIDIRON_PUBLIC_PUBLICATION == 'true'" in site
    assert "if: always()" not in site, "a failed render has no site to offer"
    runs = re.findall(r"^\s*run: (.*)$", site, re.M)
    assert runs == ["python scripts/cloud/pages_site.py build --source data/outputs/dashboard --out .site"]


def test_the_pages_artifact_is_the_packaged_directory_and_nothing_else():
    render = _section(DASH, "\n  render:", "\n  publish:")
    upload = _section(render, "      - name: Upload the Pages artifact")
    assert "if: steps.site.outcome == 'success'" in upload
    assert "actions/upload-pages-artifact@" in upload
    assert "path: .site" in upload
    for leak in ("data/outputs", "data/ledger", "data/research", "*.json", ".carry"):
        assert leak not in upload, leak


@pytest.mark.parametrize("text, name", [(DASH, "dashboard"), (SYNC, "sync")])
def test_every_action_is_pinned_to_a_full_commit_sha(text, name):
    uses = [l for l in text.splitlines() if re.match(r"^\s*uses:", l)]
    assert uses, name
    unpinned = [l.strip() for l in uses if not SHA_PIN.match(l)]
    assert not unpinned, f"{name}: {unpinned}"


@pytest.mark.parametrize("text, name", [(DASH, "dashboard"), (SYNC, "sync")])
def test_every_job_has_a_timeout_and_the_workflow_serialises_itself(text, name):
    jobs = _section(text, "\njobs:\n")
    job_starts = [m.start() for m in re.finditer(r"^  [a-z_]+:\n", jobs, re.M)]
    assert job_starts, name
    for i, start in enumerate(job_starts):
        body = jobs[start:job_starts[i + 1] if i + 1 < len(job_starts) else len(jobs)]
        assert "timeout-minutes:" in body, f"{name}: job without a timeout"
    assert "\nconcurrency:\n  group:" in text and "cancel-in-progress: false" in text, name


def test_the_workflow_states_that_public_artifacts_and_caches_are_public():
    for claim in ("PRIVATE, repo-scoped", "private artifact", "behind the repository's own access control"):
        assert claim not in DASH, f"stale privacy claim: {claim!r}"
    assert "downloadable" in DASH and "any signed-in GitHub user" in DASH
    assert "readable by any workflow run in this repository" in DASH
    assert "private snapshot" not in SYNC and "any signed-in GitHub user" in SYNC


def test_the_schedule_is_every_fifteen_minutes_and_the_dispatch_input_is_still_data():
    """15 minutes is the floor GitHub honours only loosely; the workflow's
    own comments say so, and the schedule stays a plain cron (no second
    trigger, no matrix)."""
    on = _on_block(DASH)
    crons = re.findall(r"cron: '([^']+)'", on)
    assert crons == ["7,22,37,52 * * * *"], crons
    assert "AT BEST" in DASH and "QUEUED, not when it runs" in DASH
    assert "GRIDIRON_WEEK: ${{ inputs.week }}" in DASH


# --------------------------------------------------------- the package

@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    """Both pages rendered from the synthetic pregame fixture, as the
    workflow renders them, plus the JSON records beside them."""
    out = tmp_path_factory.mktemp("render")
    s = SCN.render("pregame", out)
    assert s.get("board_rc", 0) == 0 and s["gameday_rc"] == 0, s
    d = out / "pregame"
    assert (d / pub.GAMEDAY_PAGE).exists() and (d / pub.BOARD_PAGE).exists()
    assert list(d.glob("*.json")), "the render directory holds JSON records beside the pages"
    return d


def test_the_package_is_exactly_the_allowlist_copied_verbatim(rendered, tmp_path):
    site = tmp_path / "site"
    built = pub.build_site(rendered, site)
    assert {p.name for p in site.iterdir()} == set(pub.SITE_FILES) == set(built.files)
    for name in pub.REQUIRED_PAGES:
        assert (site / name).read_bytes() == (rendered / name).read_bytes(), f"{name} was altered"
    assert not list(site.glob("*.json")) and not list(site.glob("**/*.json"))
    index = (site / pub.INDEX_PAGE).read_text("utf-8")
    assert f'content="0; url={pub.GAMEDAY_PAGE}"' in index
    assert f'href="{pub.GAMEDAY_PAGE}"' in index and f'href="{pub.BOARD_PAGE}"' in index
    assert pub.verify_site(site) == []


def test_the_pages_link_to_each_other_by_relative_name(rendered):
    gd = (rendered / pub.GAMEDAY_PAGE).read_text("utf-8")
    board = (rendered / pub.BOARD_PAGE).read_text("utf-8")
    assert f'href="{pub.BOARD_PAGE}"' in gd
    assert f'href="{pub.GAMEDAY_PAGE}"' in board
    for page in (gd, board):
        assert 'href="/' not in page, "an absolute link breaks under the /gridiron/ project path"


@pytest.mark.parametrize("missing", pub.REQUIRED_PAGES)
def test_a_missing_page_refuses_the_whole_package_and_keeps_the_previous_one(rendered, tmp_path, missing):
    site = tmp_path / "site"
    pub.build_site(rendered, site)
    before = {p.name: p.read_bytes() for p in site.iterdir()}
    src = tmp_path / "partial"
    src.mkdir()
    for name in pub.REQUIRED_PAGES:
        if name != missing:
            (src / name).write_bytes((rendered / name).read_bytes())
    with pytest.raises(pub.PublicationError, match=f"missing required page: {missing}"):
        pub.build_site(src, site)
    assert {p.name: p.read_bytes() for p in site.iterdir()} == before, "the last good site was touched"
    fresh = tmp_path / "never"
    with pytest.raises(pub.PublicationError):
        pub.build_site(src, fresh)
    assert not fresh.exists(), "a refused build must write nothing"


def test_an_empty_page_is_a_missing_page(rendered, tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    (src / pub.GAMEDAY_PAGE).write_bytes((rendered / pub.GAMEDAY_PAGE).read_bytes())
    (src / pub.BOARD_PAGE).write_bytes(b"")
    with pytest.raises(pub.PublicationError, match="empty"):
        pub.build_site(src, tmp_path / "site")


@pytest.mark.parametrize("needle", ["ghp_abcdefghijklmnop", "GRIDIRON_API_KEY=x",
                                    "data/research/cache/season2026/manifest.json",
                                    "sleeper_league_20260920T000000Z.json"])
def test_a_credential_or_private_path_in_a_page_refuses_the_package(rendered, tmp_path, needle):
    src = tmp_path / "tainted"
    src.mkdir()
    (src / pub.GAMEDAY_PAGE).write_bytes((rendered / pub.GAMEDAY_PAGE).read_bytes())
    (src / pub.BOARD_PAGE).write_bytes(
        (rendered / pub.BOARD_PAGE).read_bytes().replace(b"</main>", f"<!-- {needle} --></main>".encode()))
    site = tmp_path / "site"
    with pytest.raises(pub.PublicationError, match="refusing to package"):
        pub.build_site(src, site)
    assert not site.exists()


def test_a_stray_file_in_the_site_directory_does_not_ride_along(rendered, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "dashboard_latest.json").write_text("{}", "utf-8")
    (site / "old.png").write_bytes(b"\x89PNG")
    pub.build_site(rendered, site)
    assert {p.name for p in site.iterdir()} == set(pub.SITE_FILES)
    (site / "nested").mkdir()
    with pytest.raises(pub.PublicationError, match="directory"):
        pub.build_site(rendered, site)


def test_the_rendered_pages_carry_no_forbidden_string(rendered):
    for name in pub.REQUIRED_PAGES:
        assert pub.scan_bytes(name, (rendered / name).read_bytes()) == []


def test_the_cli_refuses_with_exit_1_and_names_no_player(rendered, tmp_path, capsys):
    site = tmp_path / "site"
    assert SITE.main(["build", "--source", str(rendered), "--out", str(site)]) == 0
    out = capsys.readouterr()
    assert all(line.startswith("[pages] ") for line in out.out.strip().splitlines())
    record = json.loads((rendered / "gameday_latest.json").read_text("utf-8"))
    names = {s.get("name") for side in ("mine", "opp") for s in record["score"][side]["starters"]
             if s.get("name")}
    assert names, "the fixture names its players"
    for n in names:
        assert n not in out.out and n not in out.err
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / pub.GAMEDAY_PAGE).write_bytes((rendered / pub.GAMEDAY_PAGE).read_bytes())
    assert SITE.main(["build", "--source", str(partial), "--out", str(tmp_path / "site2")]) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err and "last good site stays up" in err
    assert not (tmp_path / "site2").exists()


def test_the_site_directory_is_ignored_by_git():
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", ".site/index.html"], cwd=ROOT)
    assert r.returncode == 0, ".site/ would be committed"


# ----------------------------------------------------------- the browser

@pytest.mark.skipif(SITE.chrome_binary() is None, reason="no headless Chromium on this machine")
def test_the_served_site_opens_game_day_links_both_ways_and_fits_a_phone(tmp_path):
    """The package served under the project path /gridiron/, opened at 375 px:
    the root lands on Game Day, Game Day's script runs from the site origin
    and its refresh button survives one tap (against an unreachable host
    here, so the page keeps its last good state), the board opens from Game
    Day and links back, neither page scrolls sideways, and no JSON record is
    served beside the pages."""
    board_cli = SCN._load("pub_board_cli", "scripts/weekly/dashboard.py")
    gd_cli = SCN._load("pub_gd_cli", "scripts/weekly/gameday.py")
    root = tmp_path / "cache"
    SCN.build(root, "pregame")
    pages = tmp_path / "pages"
    archive = tmp_path / "archive"
    assert board_cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                           "--anonymous", "--out-dir", str(pages), "--archive-root", str(archive),
                           "--now", SCN.SATURDAY.isoformat()]) == 0
    assert gd_cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                        "--anonymous", "--out-dir", str(pages), "--archive-root", str(archive),
                        "--now", SCN._now_for("pregame").isoformat(),
                        "--api-base", "http://127.0.0.1:9"]) == 0
    site = tmp_path / "site"
    pub.build_site(pages, site)
    result = SITE.check(site, 375)
    assert result["executed"], result
    assert SITE.verdict(result) == [], result
    root_step = result["steps"][0]
    assert root_step["landed"].endswith("/gridiron/" + pub.GAMEDAY_PAGE)
    # the frame is the phone; the document is the frame minus any scrollbar
    assert root_step["fit"]["viewport"] == 375 and root_step["fit"]["overflow"] == 0
    assert 360 <= root_step["fit"]["clientWidth"] <= 375
    assert result["gameday"]["tap"]["hasState"]
    assert "last good kept" in result["gameday"]["tap"]["status"]
    board = result["steps"][1]
    assert board["page"] == "board" and board["landed"] == "/gridiron/" + pub.BOARD_PAGE
    assert result["steps"][2]["landed"] == "/gridiron/" + pub.GAMEDAY_PAGE
    assert result["jsonStatus"] == 404 and result["jsonStatus2"] == 404


@pytest.mark.parametrize("style", ["posix", "windows"])
def test_hosted_archive_paths_stay_out_of_both_public_pages(tmp_path, monkeypatch, style):
    """Real hosted paths, unlike the original fixture directory, contain data/ledger.
    Keep the immutable archive identifier, never the runner's directory tree.
    """
    from dataclasses import replace
    from pathlib import PurePosixPath, PureWindowsPath
    from gridiron import dashboard, gameday

    filename = "week03_20260926T120000Z_12345678.json"
    archive = (PurePosixPath("/home/runner/work/gridiron/gridiron/data/ledger/decisions/season2026")
               if style == "posix" else
               PureWindowsPath("C:/Users/Owner/gridiron/data/ledger/decisions/season2026")) / filename
    board_render, game_render = dashboard.render_html, gameday.render_gameday_html

    def board(d, **kwargs):
        return board_render(replace(d, archive=archive), **kwargs)

    def game(d, **kwargs):
        assert d.pregame.found
        return game_render(replace(d, pregame=replace(d.pregame, path=archive)), **kwargs)

    monkeypatch.setattr(dashboard, "render_html", board)
    monkeypatch.setattr(gameday, "render_gameday_html", game)
    result = SCN.render("pregame", tmp_path / "render")
    assert result["board_rc"] == result["gameday_rc"] == 0
    source = tmp_path / "render" / "pregame"
    for name in pub.REQUIRED_PAGES:
        html = (source / name).read_text("utf-8")
        assert filename in html
        assert str(archive) not in html
        assert "data/ledger/" not in html and "data\\ledger\\" not in html
    built = pub.build_site(source, tmp_path / "site")
    assert pub.verify_site(built.site) == []
