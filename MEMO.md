# Decision memo: the NYC congestion fee

**To:** Marketplace pricing lead, a ride-hail platform (portfolio exercise)
**From:** Sam Duong
**Decision needed:** Should we absorb the $1.50 per-trip congestion fee for riders?

## Recommendation

**Do not absorb the fee.** Pass it through as a line item, and move the effort to retraining
trip-time estimates for the zone, where the fee changed something that matters.

## Why

1. **Riders did not cut back.** Trips into, out of and within the zone did not measurably
   change after the fee started: −0.9% (95% CI −3.8% to +2.1%) relative to outer-borough trips.
   The fee is 4.3% of the average fare, so demand is inelastic here (elasticity around −0.2,
   and no more elastic than −0.9). This holds for Uber alone, for February and March alone, and
   against a second control group.
2. **Absorbing it would cost far more than it could win back.** About $110M a year, to recover
   at most $27M of margin even at the pessimistic end of the estimate ($6M at the point
   estimate). That assumes, generously, that absorbing the fee wins back every trip it cost.
3. **A competitor already tried absorbing it.** Lyft credited riders the $1.50 back for all of
   January. Over January to March, Lyft's trips in the zone did no better than Uber's
   (−3.8% against +0.2%, neither significant).

## What to do instead

- **Retrain trip-time and ETA models for the zone.** Trips inside the zone got 5.1% faster,
  and 6.0% faster in the daytime, when private cars pay the $9 toll. The weekly estimate was
  positive in every week after the fee started, and the same test on the previous winter shows nothing (−0.6%).
  Models fit on 2024 traffic will overestimate those trips by about 6%, which feeds straight
  into upfront prices, matching, and driver earnings estimates.
- **Don't act on the wait-time or fare-per-mile changes yet.** Both looked significant
  (p = 0.015 and p = 0.009), but the same test also finds significant changes in the winter
  before the fee, so the data cannot attribute them to it. Revisit with a second placebo year.

## How we know

344 million taxi and Uber/Lyft trips from the city's public trip records. Each day is compared
with the same weekday a year earlier, trips in the zone against trips outside Manhattan,
before and after 5 January 2025. The full method, charts, checks and limitations are in the
[README](README.md).
