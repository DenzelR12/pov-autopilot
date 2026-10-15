"""Business case model.

Every number here is an input the champion can change in front of their
CFO. Defaults are conservative placeholders, not benchmarks; the report
prints each assumption next to the result it drives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .discover import Discovery


@dataclass
class Assumptions:
    engineer_cost_per_hour: float = 100.0
    minutes_lost_per_interruption: float = 10.0
    off_hours_multiplier: float = 1.5      # a 3am page costs more than a 2pm one
    current_mttr_min: float = 60.0
    mttr_improvement: float = 0.20         # fraction, conservative
    customer_impacting_share: float = 0.05 # share of pages that are real customer impact
    downtime_cost_per_min: float = 500.0   # replace with the customer's own figure
    responders_per_incident: float = 2.0
    seats: int = 0
    price_per_seat_month: float = 0.0      # leave 0 to omit payback


@dataclass
class BusinessCase:
    assumptions: Assumptions
    interruptions_avoided_month: float
    hours_reclaimed_month: float
    toil_savings_year: float
    downtime_min_avoided_year: float
    downtime_savings_year: float
    responder_savings_year: float
    total_value_year: float
    license_cost_year: float
    payback_months: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["assumptions"] = asdict(self.assumptions)
        return d


def build(d: Discovery, a: Assumptions | None = None) -> BusinessCase:
    a = a or Assumptions()
    days = max(d.window["days"], 1.0)
    per_month = 30.0 / days

    # Conservative: assume the current tool already notifies once per open
    # alert, so only deduplicated alerts that would not page count as avoided.
    today = d.funnel[1][1]
    avoided = max(today - d.pages, 0) * per_month
    off = d.off_hours_share
    weighted = avoided * ((1 - off) + off * a.off_hours_multiplier)
    hours = weighted * a.minutes_lost_per_interruption / 60
    toil_year = hours * a.engineer_cost_per_hour * 12

    pages_month = d.pages * per_month
    impact_incidents_year = pages_month * 12 * a.customer_impacting_share
    mins_saved = a.current_mttr_min * a.mttr_improvement
    downtime_avoided = impact_incidents_year * mins_saved
    downtime_year = downtime_avoided * a.downtime_cost_per_min
    responder_year = (pages_month * 12 * mins_saved / 60
                      * a.responders_per_incident * a.engineer_cost_per_hour)

    total = toil_year + downtime_year + responder_year
    license_year = a.seats * a.price_per_seat_month * 12
    payback = (license_year / total * 12) if license_year and total else None

    return BusinessCase(
        assumptions=a,
        interruptions_avoided_month=round(avoided, 1),
        hours_reclaimed_month=round(hours, 1),
        toil_savings_year=round(toil_year),
        downtime_min_avoided_year=round(downtime_avoided),
        downtime_savings_year=round(downtime_year),
        responder_savings_year=round(responder_year),
        total_value_year=round(total),
        license_cost_year=round(license_year),
        payback_months=round(payback, 1) if payback else None,
    )
