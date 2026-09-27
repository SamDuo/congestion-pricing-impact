"""The estimators recover a planted effect, find none when there is none, and match days correctly."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stats import did, event_study, holm, matched_yoy, usable_days, week_block_bootstrap  # noqa: E402

TREAT = pd.Timestamp("2025-01-05")


def synthetic_gap(effect: float, seed: int = 0) -> pd.Series:
    """Daily gap series with weekday pattern, AR(1) noise, and a step of `effect` at TREAT."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2024-11-01", "2025-03-30", freq="D")
    noise = np.zeros(len(days))
    for i in range(1, len(days)):
        noise[i] = 0.5 * noise[i - 1] + rng.normal(0, 0.02)
    weekday = 0.01 * np.sin(days.dayofweek.to_numpy())
    step = np.where(days >= TREAT, effect, 0.0)
    return pd.Series(weekday + noise + step, index=days)


def test_did_recovers_planted_effect():
    est = did(synthetic_gap(-0.08), TREAT)
    assert est.ci_low < -0.08 < est.ci_high
    assert est.p_value < 0.001
    assert est.pct == pytest.approx(np.expm1(est.log_points))


def test_did_finds_nothing_when_nothing_happened():
    hits = sum(did(synthetic_gap(0.0, seed=s), TREAT).p_value < 0.05 for s in range(40))
    assert hits <= 6  # about 5% false positives expected; HAC keeps it near nominal


def test_bootstrap_interval_covers_planted_effect():
    lo, hi = week_block_bootstrap(synthetic_gap(-0.08), TREAT, reps=500)
    assert lo < -0.08 < hi


def test_event_study_is_flat_before_and_steps_after():
    es = event_study(synthetic_gap(-0.08), TREAT)
    assert es.loc[es.week < 0, "mean"].abs().max() < 0.05
    assert es.loc[es.week >= 2, "mean"].mean() == pytest.approx(-0.08, abs=0.02)


def test_matched_yoy_compares_same_weekday_a_year_earlier():
    days = pd.date_range("2023-11-01", "2025-03-31", freq="D")
    values = pd.Series(np.where(days.year == 2025, 110.0, 100.0), index=days)
    target = pd.DatetimeIndex(["2025-02-03"])  # a Monday
    y = matched_yoy(values, target)
    assert (target[0] - pd.Timedelta(days=364)).dayofweek == target[0].dayofweek
    assert y.iloc[0] == pytest.approx(np.log(1.1))


def test_usable_days_drop_holidays_on_either_side():
    days = pd.DatetimeIndex(["2024-11-28", "2024-11-20", "2024-11-13", "2025-01-10", "2025-01-20"])
    kept = usable_days(days)
    assert pd.Timestamp("2024-11-28") not in kept   # Thanksgiving 2024
    assert pd.Timestamp("2024-11-20") not in kept   # matched day is Thanksgiving eve 2023
    assert pd.Timestamp("2024-11-13") in kept
    assert pd.Timestamp("2025-01-20") not in kept   # MLK Day 2025
    assert pd.Timestamp("2025-01-10") in kept


def test_holm_matches_hand_calculation():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03)
    assert adj["c"] == pytest.approx(0.06)
    assert adj["b"] == pytest.approx(0.06)  # monotone: never below the previous step
