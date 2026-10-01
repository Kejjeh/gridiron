"""Helpers the research backtests share (never imported by src/gridiron).

Every script under scripts/research used to carry its own copy of the file
loader and the stack-fitting helpers; one bug fix then had six homes. They
live here. Scripts run as files (`python scripts/research/x.py`), so this
directory is on sys.path and `import research_common` works without a package.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT


def load_script(name: str, rel: str):
    """Import a script by repository-relative path as a module named `name`."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fit_pos(train: pd.DataFrame, cols: dict, target: str, lam: float = 5.0) -> dict:
    """One ridge per skill position on `cols[pos]` to `target`."""
    return {p: A.Ridge.fit(train.loc[train["position"] == p],
                           train.loc[train["position"] == p, target], cols[p], lam=lam)
            for p in A.POSITIONS}


def predict_pos(models: dict, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=df.index, dtype="float64")
    for p, m in models.items():
        sel = df["position"] == p
        out[sel] = m.predict(df[sel])
    return out


def lad_line(x: np.ndarray, y: np.ndarray, iters: int = 60) -> tuple[float, float]:
    """a, b minimising sum |y - a - b x| (median regression, IRLS); b >= 1e-6."""
    a, b = 0.0, 1.0
    for _ in range(iters):
        w = 1.0 / np.maximum(np.abs(y - a - b * x), 0.05)
        X = np.column_stack([np.ones_like(x), x])
        beta = np.linalg.solve((X * w[:, None]).T @ X + 1e-9 * np.eye(2), (X * w[:, None]).T @ y)
        a, b = float(beta[0]), float(beta[1])
    return a, max(b, 1e-6)


def calibrate(train_pred: pd.Series, train: pd.DataFrame, test_pred: pd.Series,
              test: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Per position, map a prediction through the median line fit on out-of-
    fold training predictions. Monotone within position, so start/sit
    ordering is untouched; only the error changes."""
    out = pd.Series(index=test.index, dtype="float64")
    lines = {}
    for p in A.POSITIONS:
        tr, te = (train["position"] == p).to_numpy(), test["position"] == p
        a, b = lad_line(np.asarray(train_pred, dtype=float)[tr],
                        train.loc[tr, "actual"].to_numpy(dtype=float))
        lines[p] = (round(a, 3), round(b, 3))
        out[te] = a + b * test_pred[te]
    return out.clip(lower=0), lines
