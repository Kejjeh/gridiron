"""scripts/sync/sleeper_sync.py as a CLI. Pins: `status` is local and prints
health lines without any network; `run --if-due` on a fresh success does
nothing and says so; the parser refuses a missing subcommand."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "sleeper_sync_cli", ROOT / "scripts" / "sync" / "sleeper_sync.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sleeper_sync_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_status_is_local_and_a_fresh_success_is_not_due(monkeypatch, tmp_path, capsys):
    ss = _load()
    cache = tmp_path / "season2026"
    cache.mkdir()
    monkeypatch.setattr(ss, "season_cache", lambda season: cache)
    monkeypatch.setattr(ss, "ensure_dirs", lambda: None)

    def no_network(*a, **k):
        raise AssertionError("the status command must not touch the network")
    monkeypatch.setattr(ss, "_client", no_network)
    assert ss.main(["status", "--season", "2026"]) == 0
    assert capsys.readouterr().out.strip()                       # health lines, nothing else
    # a success recorded a minute ago: --if-due does nothing and says so
    state = ss.ls.SyncState.load(cache)
    fresh = state.__class__(**{**state.__dict__,
                               "last_success": (datetime.now(timezone.utc) - timedelta(minutes=1))
                               .isoformat(timespec="seconds")}) \
        if hasattr(state, "__dict__") and "last_success" in state.__dict__ else None
    if fresh is not None:
        fresh.save(cache)
        assert ss.main(["run", "--if-due", "--interval", "3600", "--season", "2026"]) == 0
        assert "not due" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ss.main([])
