"""scripts/weekly/fetch_record.py: read-only GitHub fetch of the cloud record.
Pins: the newest SUCCESSFUL run on main is chosen; a run without the artifact
is refused with a message; nothing is downloaded by these tests."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "fetch_record", ROOT / "scripts" / "weekly" / "fetch_record.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_record"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_latest_run_and_artifact_come_from_the_api_and_say_when_missing(monkeypatch):
    fr = _load()
    calls = []

    def fake_get(url, *, accept="application/vnd.github+json"):
        calls.append(url)
        if url.endswith("/runs?branch=main&status=success&per_page=5"):
            return json.dumps({"workflow_runs": [{"id": 9, "run_number": 42, "conclusion": "success"}]}).encode()
        if url.endswith("/runs/9/artifacts"):
            return json.dumps({"artifacts": [{"name": "other", "id": 1},
                                             {"name": fr.ARTIFACT, "id": 7, "expired": True},
                                             {"name": fr.ARTIFACT, "id": 8}]}).encode()
        if url.endswith("/runs/5/artifacts"):
            return json.dumps({"artifacts": []}).encode()
        raise AssertionError(url)
    monkeypatch.setattr(fr, "_get", fake_get)
    assert fr.latest_run("o/r")["run_number"] == 42
    assert "branch=main" in calls[0] and "status=success" in calls[0]
    assert fr.artifact_id("o/r", 9) == 8                          # the unexpired one
    with pytest.raises(SystemExit, match="no unexpired"):
        fr.artifact_id("o/r", 5)


def test_main_reports_the_record_path_and_fails_when_the_artifact_has_none(monkeypatch, tmp_path, capsys):
    fr = _load()
    run = {"id": 9, "run_number": 42, "conclusion": "failure", "event": "schedule",
           "head_sha": "abcdef123456", "created_at": "2026-10-01T00:00:00Z"}
    monkeypatch.setattr(fr, "run_info", lambda repo, run_id: run)

    def fake_download(repo, run_id, target):
        target.mkdir(parents=True, exist_ok=True)
        (target / "outputs" / "dashboard").mkdir(parents=True)
        (target / "outputs" / "dashboard" / "dashboard_latest.json").write_text("{}")
        return "fake"
    monkeypatch.setattr(fr, "download", fake_download)
    assert fr.main(["--run", "9", "--dest", str(tmp_path), "--repo", "o/r"]) == 0
    out, err = capsys.readouterr()
    assert "run #42" in out and "dashboard_latest.json" in out
    assert "concluded 'failure'" in err                            # said, not hidden
    monkeypatch.setattr(fr, "download", lambda repo, run_id, target: (target.mkdir(parents=True, exist_ok=True), "fake")[1])
    assert fr.main(["--run", "9", "--dest", str(tmp_path / "b"), "--repo", "o/r"]) == 1
    assert "NOT FOUND" in capsys.readouterr().out
