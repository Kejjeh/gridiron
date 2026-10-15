"""Ensure the in-repo pytest basetemp parent exists (see pyproject: we keep
tmp dirs out of the machine-wide %TEMP% root, whose dead junctions crash
pytest's session teardown). pytest recreates the basetemp leaf itself but
not its parent."""
import json
from pathlib import Path

import pytest

(Path(__file__).resolve().parent.parent / ".cache").mkdir(exist_ok=True)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def crosswalk():
    from gridiron.ids import Crosswalk

    return Crosswalk.from_csv(FIXTURES / "crosswalk_small.csv")


@pytest.fixture(scope="session")
def weekly_offense():
    import pandas as pd

    return pd.read_csv(FIXTURES / "weekly_offense_wk1.csv")


@pytest.fixture(scope="session")
def weekly_kickers():
    import pandas as pd

    return pd.read_csv(FIXTURES / "weekly_kickers_wk1.csv")


@pytest.fixture(scope="session")
def snaps_wk1():
    import pandas as pd

    return pd.read_csv(FIXTURES / "snaps_wk1.csv")


@pytest.fixture(scope="session")
def schedules():
    import pandas as pd

    return pd.read_csv(FIXTURES / "schedules_wk1_2.csv")


@pytest.fixture(scope="session")
def injuries():
    import pandas as pd

    return pd.read_csv(FIXTURES / "injuries_wk1_2.csv")


@pytest.fixture(scope="session")
def league_snapshot() -> dict:
    return json.loads((FIXTURES / "sleeper_league.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def sleeper_players() -> dict:
    return json.loads(
        (FIXTURES / "sleeper_players_small.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_shipped_pwin_calibration(monkeypatch, tmp_path_factory):
    """Synthetic boards are tested against the raw closed form: the shipped
    P(win) calibration (src/gridiron/models/pwin_calibration.json) is real
    evidence about real leagues, not about fixtures. A test that wants a
    calibration writes its own file and points `CALIBRATION_PATH` at it;
    tests/test_pwin_calibration.py checks the shipped file directly."""
    from gridiron import winprob

    missing = tmp_path_factory.getbasetemp() / "no_pwin_calibration.json"
    monkeypatch.setattr(winprob, "CALIBRATION_PATH", missing)
