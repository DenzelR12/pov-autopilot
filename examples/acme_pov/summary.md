# Acme Retail: incident noise discovery

Window: 2026-09-01 to 2026-09-30 (30.0 days)

## Alert to page funnel

| Stage | Count |
|---|---:|
| Raw alert triggers | 3,643 |
| After dedup (one open alert per fingerprint) | 1,864 |
| After flap suppression | 730 |
| Grouped into incidents (5 min window per service) | 650 |
| High urgency pages (error or critical) | 213 |

**Projected reduction: 94.2%** (3,643 triggers to 213 pages)

## Recommendations

1. Send a stable dedup_key from every source: 1,779 of 3,643 triggers (49%) re-fire for an alert that is already open.
2. Suppress or delay 7 flapping checks with Event Orchestration. Worst: 'disk usage above 85' on lb-02 cycled 355 times (median 6.0 min open).
3. Model service dependencies: 10 cross service storms detected; most common likely root is 'identity'. Turn on Intelligent Alert Grouping and route the root service first.
4. Make warning and info alerts low urgency: 74% of triggers are below error severity and should not wake anyone.
5. 72% of projected pages land outside business hours: review on call load and consider support hours based urgency rules.

## Business case (annual, editable assumptions)

* Toil reclaimed: 374.0 engineer hours per month ($448,793 per year)
* Downtime avoided: 1,534 minutes ($766,800 per year)
* Faster response: $102,240 per year
* **Total modeled value: $1,317,833 per year**

## POV success plan

| Criterion | Measure | Target |
|---|---|---|
| All monitoring sources send to one global routing key | 3 sources integrated (prometheus, cloudwatch, datadog) | Week 1 |
| Noise reduction in production traffic | High urgency pages per week vs. 850 raw alerts per week today | At most 55 pages per week (94% reduction or better) |
| 'edge' team gets signal, not noise | 790 raw alerts in the sample window | At most 29 pages over the same window |
| 'search' team gets signal, not noise | 755 raw alerts in the sample window | At most 41 pages over the same window |
| 'identity' team gets signal, not noise | 702 raw alerts in the sample window | At most 36 pages over the same window |
| Replay the worst historical storm | 17 alerts across 5 services on 2026-09-05 | Grouped into 5 incidents or fewer, root 'inventory-db' paged first |
| Protect sleep | 72% of projected pages are off hours | Low urgency alerts never page off hours |
