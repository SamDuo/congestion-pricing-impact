"""Estimate what congestion pricing did to taxi and ride-hail trips, then turn it into a decision.

    python src/analyze.py        (after src/build_panel.py)

Reads results/daily_panel.csv. Writes results/estimates.json, results/estimates.csv,
results/business_case.json and the charts in charts/.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from stats import did, event_study, holm, matched_yoy, usable_days, week_block_bootstrap  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS, CHARTS = ROOT / "results", ROOT / "charts"

TREAT = pd.Timestamp("2025-01-05")                 # fee starts
SEASON = pd.date_range("2024-11-01", "2025-03-30")  # treated winter, each day matched to d - 364
PLACEBO_SHIFT = pd.Timedelta(days=364)              # same design one year earlier: no policy

CRZ_TOUCHING = ["crz_internal", "crz_boundary"]
FEE = {"rideshare": 1.50, "yellow": 0.75}          # per trip, confirmed from cbd_congestion_fee values

panel = pd.read_csv(RESULTS / "daily_panel.csv", parse_dates=["day"])


# ----------------------------------------------------------------------------- metrics
def daily(services: list[str], groups: list[str], metric: str, daypart: str = "all", company: str = "all") -> pd.Series:
    """One daily series for a set of services and trip groups (company 'all' pools Uber and Lyft)."""
    rows = panel[panel.service.isin(services) & panel.trip_group.isin(groups) & (panel.daypart == daypart)
                 & (panel.company == company)]
    if metric == "median_wait_min":
        assert len(services) == 1 and len(groups) == 1, "medians cannot be pooled"
        return rows.set_index("day")["median_wait_secs"] / 60
    s = rows.groupby("day")[["trips", "miles", "hours", "fare", "driver_pay", "cbd_fee", "trips_paying_fee"]].sum()
    return {
        "trips": s.trips,
        "speed_mph": s.miles / s.hours,
        "fare_per_mile": s.fare / s.miles,
        "driver_pay_per_trip": s.driver_pay / s.trips,
    }[metric]


# ----------------------------------------------------------------------------- outcomes
# name: (description, services, treated groups, control groups, metric, daypart[, company])
PRIMARY = {
    "rideshare_trips": ("Uber/Lyft trips touching the zone", ["rideshare"], CRZ_TOUCHING, ["outer"], "trips", "all"),
    # Taxis are compared with taxi trips in Manhattan north of the zone, not the outer boroughs: outer-borough
    # taxi trips are few, airport-heavy, and grew 25% over this window for reasons unrelated to the fee
    # (see taxi_trips_vs_outer below and the README).
    "taxi_trips": ("Yellow taxi trips touching the zone", ["yellow"], CRZ_TOUCHING, ["manhattan_north"], "trips", "all"),
    "speed_in_zone": ("Average speed of trips inside the zone", ["rideshare", "yellow"], ["crz_internal"], ["outer"], "speed_mph", "all"),
    "wait_in_zone": ("Median Uber/Lyft wait, pickups inside the zone", ["rideshare"], ["crz_internal"], ["outer"], "median_wait_min", "all"),
    "fare_per_mile": ("Uber/Lyft base fare per mile, trips touching the zone", ["rideshare"], CRZ_TOUCHING, ["outer"], "fare_per_mile", "all"),
}
SECONDARY = {
    "speed_in_zone_day": ("Speed inside the zone, car-toll daytime hours", ["rideshare", "yellow"], ["crz_internal"], ["outer"], "speed_mph", "day"),
    "speed_in_zone_night": ("Speed inside the zone, overnight", ["rideshare", "yellow"], ["crz_internal"], ["outer"], "speed_mph", "night"),
    "rideshare_trips_internal": ("Uber/Lyft trips with both ends in the zone", ["rideshare"], ["crz_internal"], ["outer"], "trips", "all"),
    "rideshare_trips_boundary": ("Uber/Lyft trips crossing the zone boundary", ["rideshare"], ["crz_boundary"], ["outer"], "trips", "all"),
    "spillover_trips_upper_manhattan": ("Uber/Lyft trips within Manhattan north of the zone", ["rideshare"], ["manhattan_north"], ["outer"], "trips", "all"),
    "spillover_speed_upper_manhattan": ("Speed within Manhattan north of the zone", ["rideshare", "yellow"], ["manhattan_north"], ["outer"], "speed_mph", "all"),
    "driver_pay_per_trip": ("Uber/Lyft driver pay per trip touching the zone", ["rideshare"], CRZ_TOUCHING, ["outer"], "driver_pay_per_trip", "all"),
    "rideshare_trips_vs_upper_manhattan": ("Uber/Lyft trips touching the zone, upper-Manhattan control", ["rideshare"], CRZ_TOUCHING, ["manhattan_north"], "trips", "all"),
    "taxi_trips_vs_outer": ("Yellow taxi trips touching the zone, outer-borough control (grew 25% on its own)", ["yellow"], CRZ_TOUCHING, ["outer"], "trips", "all"),
    # Lyft credited riders the $1.50 back during January 2025, so two checks that avoid it:
    "uber_trips": ("Uber-only trips touching the zone (no January credit)", ["rideshare"], CRZ_TOUCHING, ["outer"], "trips", "all", "uber"),
    "lyft_trips": ("Lyft-only trips touching the zone", ["rideshare"], CRZ_TOUCHING, ["outer"], "trips", "all", "lyft"),
}
# The same demand outcome with January dropped from the post period (Lyft's credit ran all January).
FEB_MAR = ("Uber/Lyft trips touching the zone, February-March only", ["rideshare"], CRZ_TOUCHING, ["outer"], "trips", "all")
CREDIT_END = pd.Timestamp("2025-02-01")


def gap_series(spec: tuple, days: pd.DatetimeIndex) -> pd.Series:
    """Treated minus control year-over-year log change, day by day."""
    _, services, treated, control, metric, daypart, *rest = spec
    company = rest[0] if rest else "all"
    days = usable_days(days)
    return (matched_yoy(daily(services, treated, metric, daypart, company), days)
            - matched_yoy(daily(services, control, metric, daypart, company), days))


def estimate(spec: tuple, placebo: bool = False, post_from: pd.Timestamp | None = None) -> dict:
    """post_from drops the start of the post period (days from TREAT up to post_from)."""
    shift = PLACEBO_SHIFT if placebo else pd.Timedelta(0)
    gap = gap_series(spec, SEASON - shift)
    if post_from is not None:
        gap = gap[(gap.index < TREAT - shift) | (gap.index >= post_from - shift)]
    est = did(gap, TREAT - shift)
    out = est.as_dict() | {"description": spec[0]}
    lo, hi = week_block_bootstrap(gap, TREAT - shift)
    out["bootstrap_pct_ci"] = [float(np.expm1(lo)), float(np.expm1(hi))]
    return out


def group_change(services, groups, metric, daypart="all") -> float:
    """Post-minus-pre change in the year-over-year gap for one group alone (not differenced)."""
    days = usable_days(SEASON)
    y = matched_yoy(daily(services, groups, metric, daypart), days).dropna()
    return float(np.expm1(y[y.index >= TREAT].mean() - y[y.index < TREAT].mean()))


# ----------------------------------------------------------------------------- run
def main() -> None:
    CHARTS.mkdir(exist_ok=True)
    results = {"primary": {}, "secondary": {}, "placebo": {}}
    for name, spec in PRIMARY.items():
        results["primary"][name] = estimate(spec)
        results["placebo"][name] = estimate(spec, placebo=True)
    for name, spec in SECONDARY.items():
        results["secondary"][name] = estimate(spec)
        results["placebo"][name] = estimate(spec, placebo=True)
    results["secondary"]["rideshare_trips_feb_mar"] = estimate(FEB_MAR, post_from=CREDIT_END)
    results["placebo"]["rideshare_trips_feb_mar"] = estimate(FEB_MAR, placebo=True, post_from=CREDIT_END)
    # A design that finds an "effect" in a year with no fee is not trusted for that outcome.
    for family in ["primary", "secondary"]:
        for name, r in results[family].items():
            r["passes_placebo"] = results["placebo"][name]["p_value"] >= 0.05

    adjusted = holm({k: v["p_value"] for k, v in results["primary"].items()})
    for k, p in adjusted.items():
        results["primary"][k]["p_holm"] = p

    # Each group on its own, so a reader can see the control did not move much.
    results["raw_changes"] = {
        "rideshare_trips_touching_zone": group_change(["rideshare"], CRZ_TOUCHING, "trips"),
        "rideshare_trips_outer_boroughs": group_change(["rideshare"], ["outer"], "trips"),
        "taxi_trips_touching_zone": group_change(["yellow"], CRZ_TOUCHING, "trips"),
        "taxi_trips_outer_boroughs": group_change(["yellow"], ["outer"], "trips"),
        "taxi_trips_upper_manhattan": group_change(["yellow"], ["manhattan_north"], "trips"),
        "speed_in_zone": group_change(["rideshare", "yellow"], ["crz_internal"], "speed_mph"),
        "speed_outer_boroughs": group_change(["rideshare", "yellow"], ["outer"], "speed_mph"),
    }

    results["first_stage"] = fee_incidence()
    business = business_case(results)
    (RESULTS / "estimates.json").write_text(json.dumps(results, indent=2))
    (RESULTS / "business_case.json").write_text(json.dumps(business, indent=2))
    table(results).to_csv(RESULTS / "estimates.csv", index=False)
    charts(results)
    print(table(results).to_string(index=False))
    print(json.dumps(business, indent=2))


def fee_incidence() -> dict:
    """Share of trips that paid the fee after it started, by group: the treatment really landed."""
    post = panel[(panel.daypart == "all") & (panel.company == "all") & (panel.day >= TREAT) & (panel.day <= SEASON[-1])]
    s = post.groupby(["service", "trip_group"])[["trips_paying_fee", "trips"]].sum()
    return {f"{svc}/{grp}": float(r.trips_paying_fee / r.trips) for (svc, grp), r in s.iterrows()}


def business_case(results: dict) -> dict:
    """Elasticity, fee revenue, and whether a ride-hail platform should absorb the fee."""
    all_rows = panel[(panel.daypart == "all") & (panel.company == "all")]
    touch = all_rows[all_rows.trip_group.isin(CRZ_TOUCHING)]
    post_days = usable_days(pd.date_range(TREAT, SEASON[-1]))
    pre_days = usable_days(pd.date_range(SEASON[0], TREAT - pd.Timedelta(days=1)))
    out = {}
    for svc, key in [("rideshare", "rideshare_trips"), ("yellow", "taxi_trips")]:
        rows = touch[touch.service == svc].groupby("day")[["trips", "fare", "driver_pay"]].sum()
        pre, post = rows.reindex(pre_days).dropna(), rows.reindex(post_days).dropna()
        fare_pre = pre.fare.sum() / pre.trips.sum()
        price_change = FEE[svc] / fare_pre
        effect = results["primary"][key]
        if not effect["passes_placebo"]:
            out[svc] = {"fee": FEE[svc], "pre_avg_base_fare": fare_pre, "fee_as_pct_of_fare": price_change,
                        "trip_effect": "inconclusive: the same test finds an effect in the placebo year"}
            continue
        out[svc] = {
            "fee": FEE[svc],
            "pre_avg_base_fare": fare_pre,
            "fee_as_pct_of_fare": price_change,
            "trip_effect_pct": effect["pct"],
            "trip_effect_ci": [effect["pct_ci_low"], effect["pct_ci_high"]],
            "arc_elasticity": effect["pct"] / price_change,
            "post_trips_per_day": post.trips.mean(),
        }
        if svc == "rideshare":
            margin = (post.fare.sum() - post.driver_pay.sum()) / post.trips.sum()
            n_post = post.trips.mean()
            lost = n_post * (1 / (1 + effect["pct"]) - 1)             # trips the fee cost per day
            lost_hi = n_post * (1 / (1 + effect["pct_ci_low"]) - 1)   # most pessimistic end of the CI
            cost = FEE[svc] * (n_post + lost)                            # absorb the fee on every trip
            out[svc] |= {
                "platform_margin_per_trip": margin,
                "trips_lost_per_day": lost,
                "trips_lost_per_day_worst_case": lost_hi,
                "absorb_fee_cost_per_year": cost * 365,
                "absorb_fee_recovered_margin_per_year": lost * margin * 365,
                "absorb_fee_recovered_margin_per_year_worst_case": lost_hi * margin * 365,
            }
    fees = all_rows[all_rows.day.isin(post_days)].groupby("day").cbd_fee.sum()
    out["fee_revenue_per_day_tlc_trips"] = float(fees.mean())
    out["fee_revenue_per_year_tlc_trips"] = float(fees.mean() * 365)
    return out


def table(results: dict) -> pd.DataFrame:
    rows = []
    for family in ["primary", "secondary"]:
        for name, r in results[family].items():
            pl = results["placebo"].get(name)
            rows.append({
                "family": family, "outcome": name, "description": r["description"],
                "effect_pct": round(100 * r["pct"], 2),
                "ci_95": f"[{100 * r['pct_ci_low']:.1f}, {100 * r['pct_ci_high']:.1f}]",
                "bootstrap_ci": f"[{100 * r['bootstrap_pct_ci'][0]:.1f}, {100 * r['bootstrap_pct_ci'][1]:.1f}]",
                "p": f"{r['p_value']:.2g}",
                "p_holm": f"{r['p_holm']:.2g}" if "p_holm" in r else "",
                "placebo_pct": round(100 * pl["pct"], 2) if pl else None,
                "placebo_p": f"{pl['p_value']:.2g}" if pl else "",
                "passes_placebo": r["passes_placebo"],
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- charts
INK, MUTED, ACCENT, SECOND, LINE = "#1f262b", "#65767f", "#1c5c84", "#c05a2b", "#d5dde2"


def style(ax, title, ylabel):
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=10)
    ax.set_ylabel(ylabel, color=MUTED)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(LINE)
    ax.spines["bottom"].set_color(LINE)
    ax.tick_params(colors=MUTED)
    ax.axhline(0, color=LINE, lw=1)
    ax.axvline(-0.5, color=MUTED, lw=1, ls="--")
    ax.text(-0.3, ax.get_ylim()[1], "fee starts", color=MUTED, fontsize=8, va="top")
    lo, hi = ax.get_ylim()
    pcts = [p for p in range(-30, 35, 5) if lo <= np.log1p(p / 100) <= hi]
    ax.set_yticks([np.log1p(p / 100) for p in pcts], [f"{p:+d}%" if p else "0" for p in pcts])


def plot_event(ax, spec, color, label, shift=pd.Timedelta(0), alpha=1.0):
    es = event_study(gap_series(spec, SEASON - shift), TREAT - shift)
    ax.plot(es.week, es["mean"], color=color, lw=2, label=label, alpha=alpha, marker="o", ms=3)
    ax.fill_between(es.week, es.low, es.high, color=color, alpha=0.12 * alpha, lw=0)


def charts(results: dict) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2))
    plot_event(ax, PRIMARY["rideshare_trips"], ACCENT, "Winter 2024-25 (fee starts 5 Jan)")
    plot_event(ax, PRIMARY["rideshare_trips"], MUTED, "Winter 2023-24 (no fee, placebo)", PLACEBO_SHIFT, 0.7)
    style(ax, "Uber/Lyft trips touching the zone did not fall after the $1.50 fee",
          "vs outer-borough trips, year over year")
    ax.set_xlabel("Weeks since the fee started (holiday weeks removed)", color=MUTED)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.tight_layout(); fig.savefig(CHARTS / "event_study_trips.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    plot_event(ax, PRIMARY["speed_in_zone"], ACCENT, "Winter 2024-25 (fee starts 5 Jan)")
    plot_event(ax, PRIMARY["speed_in_zone"], MUTED, "Winter 2023-24 (no fee, placebo)", PLACEBO_SHIFT, 0.7)
    style(ax, "Trips inside the zone got about 5% faster once the toll started",
          "vs outer-borough speeds, year over year")
    ax.set_xlabel("Weeks since the fee started (holiday weeks removed)", color=MUTED)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.tight_layout(); fig.savefig(CHARTS / "event_study_speed.png", dpi=180); plt.close(fig)

    rows = [(n, PRIMARY[n][0], "primary") for n in PRIMARY] + \
           [(n, SECONDARY[n][0], "secondary") for n in ["speed_in_zone_day", "speed_in_zone_night",
                                                          "spillover_trips_upper_manhattan", "spillover_speed_upper_manhattan"]]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    for i, (name, label, fam) in enumerate(rows):
        for r, color, dy, legend in [(results[fam][name], ACCENT, -0.14, "2025 fee"),
                                     (results["placebo"][name], MUTED, 0.14, "Same test, 2024 (no fee)")]:
            ax.errorbar(100 * r["pct"], i + dy,
                        xerr=[[100 * (r["pct"] - r["pct_ci_low"])], [100 * (r["pct_ci_high"] - r["pct"])]],
                        fmt="o", color=color, capsize=3, ms=5, label=legend if i == 0 else None)
    labels = [label + ("" if results[fam][name]["passes_placebo"] else "  (fails placebo)") for name, label, fam in rows]
    ax.set_yticks(range(len(rows)), labels, fontsize=9)
    for tick, (name, _, fam) in zip(ax.get_yticklabels(), rows):
        tick.set_color(INK if results[fam][name]["passes_placebo"] else MUTED)
    ax.invert_yaxis()
    ax.axvline(0, color=LINE, lw=1)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    ax.set_xlabel("Effect, % (95% CI)", color=MUTED)
    fig.suptitle("Each estimate next to the same test on a winter with no fee", x=0.02, ha="left", fontsize=12, color=INK)
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2)
    fig.tight_layout(); fig.savefig(CHARTS / "effects_vs_placebo.png", dpi=180); plt.close(fig)


if __name__ == "__main__":
    main()
