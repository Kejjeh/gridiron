"""The public site package: what GitHub Pages is allowed to receive.

The owner authorised publishing the repository AND the personalised pages
(2026-09-21, docs/DECISIONS.md). That authorisation covers two rendered HTML
files and nothing else, so the boundary is an allowlist of exact filenames
rather than a glob: a JSON decision record, a cache file, an archive file or
a stray screenshot next to the pages is refused by name, never uploaded by
accident. Credentials were never in the render path (rule #9) and the scan
below is the tripwire that says so on every build.

Three properties, each held by `build_site` and pinned in
tests/test_publication.py:

  * Both pages or nothing. A site with a board and no Game Day page, or the
    reverse, is not deployed: the package step fails, no Pages artifact is
    uploaded, the deploy job is skipped, and the LAST GOOD site stays up. A
    half-site would be a broken navigation on the owner's phone; an older
    complete site is a dated one, which the pages themselves say.
  * Bytes are copied verbatim. The pages carry their own "generated" stamp
    and every input's as-of; the packager never touches them, so nothing is
    restamped to look fresher than the build that made it.
  * The root opens Game Day. `index.html` is a small redirect page (with a
    plain link for a browser that will not follow a meta refresh) to
    `gameday_latest.html`; the two pages already link to each other by
    relative name, so the project path `/gridiron/` works without a base tag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: The pages the site may contain, by exact filename, and nothing else.
#: Both are required. Their names are the names the renderers write, so the
#: cross-links inside them resolve on the site as they do in the artifact.
GAMEDAY_PAGE = "gameday_latest.html"
BOARD_PAGE = "dashboard_latest.html"
REQUIRED_PAGES: tuple[str, ...] = (GAMEDAY_PAGE, BOARD_PAGE)

#: The root document, written by the packager.
INDEX_PAGE = "index.html"

#: Everything a finished site directory may hold. A test asserts the built
#: directory equals this set exactly.
SITE_FILES: frozenset[str] = frozenset((INDEX_PAGE, *REQUIRED_PAGES))

#: Strings that must not appear in any published byte. The first group is
#: the credential surface (rule #9: every secret is a GRIDIRON_-prefixed
#: environment variable read from .env); the second is the private-tree
#: surface (paths and cache filenames a page has no reason to mention).
#: Matching is case-sensitive on purpose — these are exact spellings.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "GRIDIRON_API", "GRIDIRON_TOKEN", "GRIDIRON_SECRET", "GRIDIRON_PASSWORD",
    "Authorization:", "Bearer ", "ghp_", "github_pat_",
    ".env", "data/research/cache", "data/outputs/", "data/ledger/",
    "sleeper_league_", "sync_state.json", "manifest.json", ".carry/",
)

#: A meta-refresh page: the root URL lands on Game Day without JavaScript.
#: The link is the fallback for a browser (or a reader) that will not follow
#: the refresh. Nothing here is dated; the page it opens is.
INDEX_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    f"<meta http-equiv=\"refresh\" content=\"0; url={GAMEDAY_PAGE}\">"
    "<meta name=\"robots\" content=\"noindex\">"
    "<title>gridiron</title></head><body>"
    f"<p><a href=\"{GAMEDAY_PAGE}\">Game Day</a> · <a href=\"{BOARD_PAGE}\">pregame board</a></p>"
    "</body></html>\n"
)

_LINK = re.compile(r"href=\"([^\"]+)\"")


class PublicationError(RuntimeError):
    """The package cannot be built; nothing was written to the site dir."""


@dataclass(frozen=True)
class SiteBuild:
    site: Path
    files: tuple[str, ...]
    sizes: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        return [f"{name}  {self.sizes[name]} bytes" for name in self.files]


def _check_pages(source: Path) -> list[str]:
    """Every reason the source directory cannot be published, or []."""
    problems: list[str] = []
    for name in REQUIRED_PAGES:
        p = source / name
        if not p.is_file():
            problems.append(f"missing required page: {name}")
            continue
        if p.stat().st_size == 0:
            problems.append(f"required page is empty: {name}")
    return problems


def scan_bytes(name: str, data: bytes) -> list[str]:
    """Forbidden spellings found in one file, as 'name: needle' lines."""
    hits: list[str] = []
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle.encode("utf-8") in data:
            hits.append(f"{name}: contains {needle!r}")
    return hits


def _links_resolve(name: str, data: bytes) -> list[str]:
    """Relative links inside a page must point at another allowlisted file.

    The pages link to each other by bare filename; an absolute path would
    break under the project prefix and an unlisted target would 404. External
    links (scheme) are left alone: they are the page's own business.
    """
    bad: list[str] = []
    text = data.decode("utf-8", "replace")
    for target in _LINK.findall(text):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        base = target.split("#", 1)[0].split("?", 1)[0]
        if base and base not in SITE_FILES:
            bad.append(f"{name}: link to {target!r} is not an allowlisted page")
        if target.startswith("/"):
            bad.append(f"{name}: absolute link {target!r} breaks under a project path")
    return bad


def build_site(source: Path, site: Path) -> SiteBuild:
    """Copy the two allowlisted pages verbatim into `site` and write the index.

    All checks run BEFORE anything is written; on any failure `site` is left
    exactly as it was (absent or holding the previous package), so a failed
    build can never be uploaded as a partial one. An existing `site` is
    replaced whole, never merged: a file left over from an earlier package
    would otherwise ride along outside the allowlist.
    """
    problems = _check_pages(source)
    if problems:
        raise PublicationError("; ".join(problems))
    payload: dict[str, bytes] = {}
    for name in REQUIRED_PAGES:
        payload[name] = (source / name).read_bytes()
    payload[INDEX_PAGE] = INDEX_HTML.encode("utf-8")
    hits: list[str] = []
    for name, data in payload.items():
        hits.extend(scan_bytes(name, data))
        hits.extend(_links_resolve(name, data))
    if hits:
        raise PublicationError("refusing to package: " + "; ".join(hits))

    if site.exists():
        for child in site.iterdir():
            if child.is_dir():
                raise PublicationError(f"site dir holds a directory, not replacing it: {child.name}")
            child.unlink()
    site.mkdir(parents=True, exist_ok=True)
    for name, data in payload.items():
        (site / name).write_bytes(data)
    files = tuple(sorted(payload))
    return SiteBuild(site=site, files=files, sizes={n: len(payload[n]) for n in files})


def verify_site(site: Path) -> list[str]:
    """Re-check a built directory: exactly the allowlist, no forbidden bytes,
    every link resolving. Empty list means clean."""
    problems: list[str] = []
    if not site.is_dir():
        return [f"no site directory at {site}"]
    names = {p.name for p in site.iterdir()}
    extra, missing = names - SITE_FILES, SITE_FILES - names
    if extra:
        problems.append(f"files outside the allowlist: {sorted(extra)}")
    if missing:
        problems.append(f"allowlisted files missing: {sorted(missing)}")
    for name in sorted(names & SITE_FILES):
        data = (site / name).read_bytes()
        problems.extend(scan_bytes(name, data))
        problems.extend(_links_resolve(name, data))
    return problems
