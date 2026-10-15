# POV Autopilot for PagerDuty

[![ci](https://github.com/DenzelR12/pov-autopilot/actions/workflows/ci.yml/badge.svg)](https://github.com/DenzelR12/pov-autopilot/actions/workflows/ci.yml)

**Automates the repeatable 80% of the Solutions Architect motion for incident management deals:** discovery, solution design, proof of value, the business case, and Opsgenie migration. It starts from the one artifact every prospect already has, their alert history, and turns it into a report, a success plan, and Terraform their platform team can apply on day one.

```
alert export (CSV) ──► discover ──► report.html + summary.md     (exec readout)
                          │    └──► business_case.json           (ROI, every assumption editable)
                          ├──────► POV success plan              (measurable, from their data)
                          └──────► terraform/                    (services, schedules, escalation,
                                                                  Event Orchestration routing + flap rules)
Opsgenie API / export ──► migrate ──► terraform/ + migration_gaps.md
alert export ──► replay ──► Events API v2 (their real storm, live in the POV account)
```

Zero runtime dependencies. Python 3.10+.

## Why this exists

Opsgenie reaches end of support on **April 5, 2027**, and unmigrated data is deleted after that date ([Atlassian](https://www.atlassian.com/software/opsgenie/migration)). That puts a lot of incident management evaluations on a deadline at the same time. An SA who can go from "send me last month's alerts" to a quantified readout and an applied configuration in one afternoon wins those evaluations. This repo is that afternoon, scripted.

## What it automates, mapped to the job

| SA responsibility | Manual today | Here |
|---|---|---|
| Technical discovery | Interviews, spreadsheets, guesswork about noise | `pdsa discover`: dedup, flap, storm and off hours analysis from real alerts |
| Solution design | Whiteboard, then hand built config | `pdsa pov`: teams, schedules, escalation policies, services, global Event Orchestration as Terraform |
| POV success criteria | Negotiated from opinions | Generated from the prospect's own baseline, so every target is measurable |
| Live demo | Generic sandbox data | `pdsa replay`: their worst historical storm, time compressed, into the POV account |
| Business case | Vendor calculator the CFO doesn't trust | Every dollar traced to a named, editable assumption |
| Competitive migration | Weeks of mapping rotations and rules | `pdsa migrate`: Opsgenie schedules, restrictions, escalations, services to Terraform, plus a list of every gap |

## Quickstart

```bash
pip install -e ".[dev]"

pdsa sample --out alerts.csv                       # or use a real export
pdsa pov alerts.csv --customer "Acme Retail" --out acme_pov
open acme_pov/report.html
```

Sample output for a synthetic 30 day history (see [`examples/acme_pov`](examples/acme_pov)):

| Stage | Count |
|---|---:|
| Raw alert triggers | 3,643 |
| After dedup (one open alert per fingerprint) | 1,864 |
| After flap suppression | 730 |
| Grouped into incidents (5 min window per service) | 650 |
| High urgency pages (error or critical) | 213 |

## Commands

### `pdsa discover alerts.csv [--json out.json]`
Accepts exports from Datadog, Prometheus Alertmanager, CloudWatch, Nagios and similar. Columns are matched by common aliases (`created_at`, `monitor_name`, `hostname`, `priority`, `state`, ...). Severities like `P1` or `sev2` and statuses like `OK` or `resolved` are normalized.

The funnel is built from rules a customer can audit line by line:

1. **Dedup:** a trigger for a check and host that is already open would carry the same `dedup_key`. Alerts with no resolve event time out after 4 hours.
2. **Flap suppression:** checks that open and close within 10 minutes at least 5 times.
3. **Grouping:** fixed 5 minute window per service, measured from the first alert, never chained.
4. **Urgency:** only incidents containing an error or critical alert page.

These are deliberately conservative stand ins for the platform's own grouping, so the live POV should meet or beat every number in the report. It also detects **cross service storms** (one root cause, many services) and infers service ownership.

### `pdsa pov alerts.csv --customer NAME [--out DIR] [assumption flags]`
Writes `report.html`, `summary.md`, `discovery.json`, `business_case.json`, `design.json` and `terraform/`. The Terraform uses the official `PagerDuty/pagerduty` provider and contains:

* one team, weekly schedule and two level escalation policy per discovered team, with members supplied through `var.team_members`
* one service per discovered service, with severity based urgency and intelligent alert grouping
* a **global Event Orchestration** whose router sends each event to the right service, so every monitoring tool can share one routing key
* **service orchestration rules that `suspend` flapping checks for 5 minutes**, so a check that recovers on its own never pages

Business case flags: `--engineer-cost-per-hour`, `--minutes-lost-per-interruption`, `--off-hours-multiplier`, `--current-mttr-min`, `--mttr-improvement`, `--customer-impacting-share`, `--downtime-cost-per-min`, `--responders-per-incident`, `--seats`, `--price-per-seat-month`. The report prints each assumption next to the number it drives.

### `pdsa migrate (--bundle export.json | --pull) [--region us|eu]`
`--pull` reads teams, users, schedules (with rotations), escalations and services through the Opsgenie REST API using `OPSGENIE_API_KEY`. Conversion handles the details that usually break a migration:

* **Escalation timing:** Opsgenie delays count from alert creation; PagerDuty delays are the wait before the next rule. Absolute delays `0, 10, 25` become waits `10, 15, 15`, and rules that fire at the same moment are merged into one rule with several targets.
* **Time restrictions:** `time-of-day` becomes a `daily_restriction`, `weekday-and-time-of-day` becomes `weekly_restriction`s, including spans across midnight and weekends.
* **Team recipients** map to the team's schedule; unknown users referenced only in escalations are still created.

Anything without a one to one mapping (empty "no one" slots, `notify next` on call, `if-not-closed` conditions, a first rule that waited before notifying, services whose team has no policy) is written to `migration_gaps.md` so the SA walks the customer through each decision. See [`examples/acme_migration`](examples/acme_migration).

### `pdsa replay alerts.csv [--storm N] [--speed 60] [--send]`
Sends history to the Events API v2 with stable dedup keys, original timestamps in `custom_details`, and `source`, `component`, `group` and `class` set so routing and grouping work. `--storm 0` picks the largest historical storm. **Dry run by default**; nothing leaves the machine without `--send`, and the routing key is redacted in dry run output.

## Typical POV, end to end

```bash
pdsa pov datadog_export.csv --customer "Acme Retail" --seats 40 --out acme
cd acme/terraform && cp terraform.tfvars.example terraform.tfvars   # add token and team members
terraform init && terraform apply
export PD_ROUTING_KEY=$(terraform output -raw global_routing_key)
cd ../.. && pdsa replay datadog_export.csv --storm 0 --send          # watch the storm collapse live
```

## Tests

```bash
pytest -q
```

21 tests cover normalization, every funnel stage, Opsgenie delay and restriction math, gap reporting, Events API payloads, key redaction, and HCL validity of all generated Terraform. CI also runs `terraform validate` against both example outputs.

## Limits

* Reduction numbers are estimates from simple rules, not a prediction of the platform's ML grouping.
* The business case is a model. Defaults are placeholders, not benchmarks.
* Opsgenie alert policies, integrations and heartbeats are inventoried, not converted.

Independent project. Not affiliated with or endorsed by PagerDuty or Atlassian.

## License

MIT
