"""Estimators for the congestion-pricing impact analysis.

The design is a triple difference on daily data:

1. Year over year, weekday matched: compare each day with the same weekday 52 weeks
   earlier (d - 364), which removes seasonality and day-of-week patterns.
2. Treated minus control: subtract the control group's year-over-year change on the same
   day, which removes citywide shocks such as weather and the economy.
3. After minus before: compare that gap after the policy date with the gap before it.

Everything is in log points, so an estimate b reads as a (exp(b) - 1) percent change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import statsmodels.api as sm

MATCH_LAG_DAYS = 364  # 52 weeks, so every day is compared with the same weekday a year earlier

# Days whose behaviour is dominated by holidays. A day is dropped when it, or its matched
# day a year earlier, falls in one of these windows, so holidays never sit on one side only.
HOLIDAY_WINDOWS = [
    # Thanksgiving, Wednesday to Sunday
    (date(2022, 11, 23), date(2022, 11, 27)),
    (date(2023, 11, 22), date(2023, 11, 26)),
    (date(2024, 11, 27), date(2024, 12, 1)),
    # Christmas to New Year
    (date(2022, 12, 20), date(2023, 1, 4)),
    (date(2023, 12, 20), date(2024, 1, 4)),
    (date(2024, 12, 20), date(2025, 1, 4)),
    # Monday holidays: MLK Day and Presidents' Day
    (date(2023, 1, 16), date(2023, 1, 16)),
    (date(2023, 2, 20), date(2023, 2, 20)),
    (date(2024, 1, 15), date(2024, 1, 15)),
    (date(2024, 2, 19), date(2024, 2, 19)),
    (date(2025, 1, 20), date(2025, 1, 20)),
    (date(2025, 2, 17), date(2025, 2, 17)),
]


def is_holiday(d: date) -> bool:
    return any(lo <= d <= hi for lo, hi in HOLIDAY_WINDOWS)


def usable_days(days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Days where neither the day nor its matched day a year earlier is a holiday."""
    keep = [d for d in days
            if not is_holiday(d.date()) and not is_holiday((d - timedelta(days=MATCH_LAG_DAYS)).date())]
    return pd.DatetimeIndex(keep)


def matched_yoy(series: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """log(value on day d) - log(value on day d - 364), for each d in days."""
    series = series.copy()
    series.index = pd.DatetimeIndex(series.index)
    now = series.reindex(days)
    before = series.reindex(days - pd.Timedelta(days=MATCH_LAG_DAYS))
    return pd.Series(np.log(now.to_numpy()) - np.log(before.to_numpy()), index=days)


@dataclass
class Estimate:
    log_points: float
    se: float
    p_value: float
    ci_low: float
    ci_high: float
    n_pre: int
    n_post: int

    @property
    def pct(self) -> float:
        return float(np.expm1(self.log_points))

    @property
    def pct_ci(self) -> tuple[float, float]:
        return float(np.expm1(self.ci_low)), float(np.expm1(self.ci_high))

    def as_dict(self) -> dict:
        lo, hi = self.pct_ci
        return {"log_points": self.log_points, "se": self.se, "p_value": self.p_value,
                "pct": self.pct, "pct_ci_low": lo, "pct_ci_high": hi,
                "n_pre_days": self.n_pre, "n_post_days": self.n_post}


def did(gap: pd.Series, treat_day: pd.Timestamp, hac_lags: int = 7) -> Estimate:
    """Mean of the treated-minus-control gap after treat_day minus its mean before.

    OLS of the gap on a post indicator and day-of-week dummies, with Newey-West (HAC)
    standard errors so that serial correlation across neighbouring days is allowed for.
    """
    gap = gap.dropna()
    frame = pd.DataFrame({"gap": gap.to_numpy()}, index=gap.index)
    frame["post"] = (frame.index >= treat_day).astype(float)
    dow = pd.get_dummies(frame.index.dayofweek, prefix="dow", drop_first=True, dtype=float)
    dow.index = frame.index
    X = sm.add_constant(pd.concat([frame[["post"]], dow], axis=1))
    fit = sm.OLS(frame["gap"], X).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    b, se = float(fit.params["post"]), float(fit.bse["post"])
    lo, hi = fit.conf_int().loc["post"]
    return Estimate(b, se, float(fit.pvalues["post"]), float(lo), float(hi),
                    int((frame["post"] == 0).sum()), int((frame["post"] == 1).sum()))


def week_block_bootstrap(gap: pd.Series, treat_day: pd.Timestamp, reps: int = 2000,
                         seed: int = 7) -> tuple[float, float]:
    """95% interval for the post-minus-pre difference in means, resampling whole weeks.

    Weeks are resampled within the pre and post periods separately, which keeps each
    week's internal correlation intact. A cross-check on the HAC interval.
    """
    gap = gap.dropna()
    rng = np.random.default_rng(seed)
    week = gap.index.to_period("W-SAT")
    pre_weeks = [gap[(week == w) & (gap.index < treat_day)].to_numpy() for w in week.unique()]
    post_weeks = [gap[(week == w) & (gap.index >= treat_day)].to_numpy() for w in week.unique()]
    pre_weeks = [w for w in pre_weeks if len(w)]
    post_weeks = [w for w in post_weeks if len(w)]
    draws = np.empty(reps)
    for i in range(reps):
        pre = np.concatenate([pre_weeks[j] for j in rng.integers(len(pre_weeks), size=len(pre_weeks))])
        post = np.concatenate([post_weeks[j] for j in rng.integers(len(post_weeks), size=len(post_weeks))])
        draws[i] = post.mean() - pre.mean()
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def event_study(gap: pd.Series, treat_day: pd.Timestamp) -> pd.DataFrame:
    """Weekly mean of the gap relative to its pre-period mean, with a 95% band.

    Week 0 is the week starting on treat_day. The band uses the within-week standard error,
    which is a display aid, not the inference (use did() for that).
    """
    gap = gap.dropna()
    base = gap[gap.index < treat_day].mean()
    rel_week = np.floor((gap.index - treat_day).days / 7).astype(int)
    frame = pd.DataFrame({"gap": gap.to_numpy() - base, "week": rel_week})
    out = frame.groupby("week")["gap"].agg(["mean", "std", "count"])
    out["se"] = out["std"] / np.sqrt(out["count"])
    out["low"] = out["mean"] - 1.96 * out["se"]
    out["high"] = out["mean"] + 1.96 * out["se"]
    return out.reset_index()


def holm(p_values: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values for one family of tests."""
    names = sorted(p_values, key=p_values.get)
    m = len(names)
    adjusted, running = {}, 0.0
    for i, name in enumerate(names):
        running = max(running, min(1.0, (m - i) * p_values[name]))
        adjusted[name] = running
    return adjusted
