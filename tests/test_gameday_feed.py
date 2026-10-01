"""The per-game status feed: validated, recorded beside the snapshot, and a
failure never touches the last good copy."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import livesync as ls
from gridiron.ingest import Manifest

T0 = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)

GOOD = [
    {"week": 3, "home": "BAL", "away": "NO", "status": "complete", "date": "2026-09-27",
     "game_id": "202610301"},
    {"week": 3, "home": "KC", "away": "IND", "status": "pre_game", "date": "2026-09-27",
     "game_id": "202610302"},
]


class Client:
    def __init__(self, payload=None, raises=None):
        self.payload, self.raises = payload, raises

    def schedule(self, season):
        if self.raises:
            raise self.raises
        return self.payload


def test_a_good_feed_is_recorded_with_its_own_as_of(tmp_path: Path):
    res = ls.refresh_game_status(Client(GOOD), tmp_path, now=T0, season=2026)
    assert res.ok and res.published, res.detail
    entry = Manifest.load(tmp_path, 2026).get(ls.GAME_STATUS_NAME)
    assert entry is not None and entry.rows == 2 and entry.weeks == [3]
    assert entry.as_of_dt == T0 and not entry.error
    blob = json.loads((tmp_path / ls.GAME_STATUS_FILE).read_text("utf-8"))
    assert blob["as_of"] == T0.isoformat(timespec="seconds")
    assert [g["status"] for g in blob["games"]] == ["complete", "pre_game"]


def test_a_failed_fetch_keeps_the_last_good_copy_and_marks_the_failure(tmp_path: Path):
    ls.refresh_game_status(Client(GOOD), tmp_path, now=T0, season=2026)
    before = (tmp_path / ls.GAME_STATUS_FILE).read_bytes()
    later = T0 + timedelta(hours=1)
    res = ls.refresh_game_status(Client(raises=TimeoutError("boom")), tmp_path, now=later,
                                 season=2026)
    assert not res.ok and res.code == "fetch_failed"
    assert (tmp_path / ls.GAME_STATUS_FILE).read_bytes() == before
    entry = Manifest.load(tmp_path, 2026).get(ls.GAME_STATUS_NAME)
    assert entry.as_of_dt == T0                      # the good pull's time survives
    assert "boom" in entry.error and entry.last_attempt.startswith("2026-09-27T18:00")


def test_a_payload_that_is_not_a_list_is_refused_whole(tmp_path: Path):
    for bad in ({"games": GOOD}, "text", None, 42):
        res = ls.refresh_game_status(Client(bad), tmp_path, now=T0, season=2026)
        assert not res.ok and res.code == "invalid", bad
    assert not (tmp_path / ls.GAME_STATUS_FILE).exists()


def test_malformed_rows_are_dropped_and_counted_and_the_rest_kept():
    rows, dropped, why = ls.validate_game_status(
        GOOD + [None, {"week": "x", "home": "A", "away": "B", "status": "s"},
                {"week": 3, "home": "SEA", "away": "SEA", "status": "in_game"},
                {"week": 3, "home": "sea", "away": "ari", "status": "IN_GAME"}])
    assert why == "" and dropped == 3
    assert rows[-1] == {"week": 3, "home": "SEA", "away": "ARI", "status": "in_game",
                        "date": "", "game_id": ""}


def test_a_feed_with_no_usable_row_is_refused():
    rows, dropped, why = ls.validate_game_status([None, {"week": "x"}])
    assert rows == [] and dropped == 2 and "no usable" in why


def test_an_unrecognised_status_word_is_kept_verbatim_and_named(tmp_path: Path):
    res = ls.refresh_game_status(
        Client(GOOD + [{"week": 3, "home": "GB", "away": "NYJ", "status": "postponed"}]),
        tmp_path, now=T0, season=2026)
    assert res.ok and "postponed" in res.detail
    blob = json.loads((tmp_path / ls.GAME_STATUS_FILE).read_text("utf-8"))
    assert blob["games"][-1]["status"] == "postponed"


def test_a_client_without_the_feed_is_recorded_as_unsupported_not_raised(tmp_path: Path):
    class NoFeed:
        pass

    res = ls.refresh_game_status(NoFeed(), tmp_path, now=T0, season=2026)
    assert not res.ok and res.code == "unsupported"
    entry = Manifest.load(tmp_path, 2026).get(ls.GAME_STATUS_NAME)
    assert entry is not None and entry.error and entry.as_of == ""


def test_the_league_snapshot_is_not_touched_by_the_feed(tmp_path: Path):
    """The feed lives beside `sleeper_league` and must never write over it."""
    m = Manifest(tmp_path, season=2026)
    (tmp_path / "sleeper_league_x.json").write_text("{}", "utf-8")
    m.record("sleeper_league", path=tmp_path / "sleeper_league_x.json", rows=12,
             source="test", as_of=T0 - timedelta(minutes=5), weeks=[3])
    m.save()
    ls.refresh_game_status(Client(GOOD), tmp_path, now=T0, season=2026)
    league = Manifest.load(tmp_path, 2026).get("sleeper_league")
    assert league.path == "sleeper_league_x.json" and league.as_of_dt == T0 - timedelta(minutes=5)
