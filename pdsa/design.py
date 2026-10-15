"""A tool neutral 'target design': teams, people, schedules, escalation
policies, services and event rules.

Discovery output and an Opsgenie export are both converted into this shape,
and the Terraform renderer only ever reads this shape. That keeps the
migration path and the greenfield path on one code path.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .discover import Discovery


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s if s and not s[0].isdigit() else f"r_{s}"


@dataclass
class Layer:
    name: str
    start: str  # RFC3339
    rotation_seconds: int
    users: list[str] = field(default_factory=list)  # emails
    team_var: str = ""  # when set, members come from var.team_members[team_var]
    # PagerDuty layer restrictions: {"type": "daily_restriction"|"weekly_restriction",
    #  "start_time_of_day": "HH:MM:SS", "duration_seconds": int, "start_day_of_week": 1..7}
    restrictions: list[dict] = field(default_factory=list)


@dataclass
class Schedule:
    name: str
    team: str
    time_zone: str
    layers: list[Layer]


@dataclass
class Target:
    kind: str  # "schedule" or "user"
    ref: str   # schedule name or user email


@dataclass
class EscalationRule:
    delay_min: int
    targets: list[Target]


@dataclass
class EscalationPolicy:
    name: str
    team: str
    rules: list[EscalationRule]
    num_loops: int = 2


@dataclass
class Service:
    name: str
    team: str
    escalation_policy: str
    description: str = ""
    route_expression: str = ""
    suspend_rules: list[dict] = field(default_factory=list)  # {"label","expression","seconds"}
    severity_based_urgency: bool = True
    intelligent_grouping: bool = True


@dataclass
class Design:
    name: str
    teams: list[str]
    users: list[dict]  # {"email","name"}; created as resources when create_users
    schedules: list[Schedule]
    escalation_policies: list[EscalationPolicy]
    services: list[Service]
    create_users: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _pcl_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def from_discovery(d: Discovery, customer: str, time_zone: str = "America/New_York",
                   rotation_start: str = "2026-11-02T09:00:00-05:00") -> Design:
    teams = sorted({s["team"] for s in d.services.values()})
    schedules, policies, services = [], [], []
    for team in teams:
        sched = f"{team} primary on call"
        schedules.append(Schedule(
            name=sched, team=team, time_zone=time_zone,
            layers=[Layer(name="weekly rotation", start=rotation_start,
                          rotation_seconds=7 * 24 * 3600, team_var=team)],
        ))
        policies.append(EscalationPolicy(
            name=f"{team} escalation", team=team,
            rules=[EscalationRule(15, [Target("schedule", sched)]),
                   EscalationRule(15, [Target("schedule", sched)])],
        ))

    flaps_by_service: dict[str, list[dict]] = {}
    for f in d.flapping:
        flaps_by_service.setdefault(f["service"], []).append(f)

    for name, info in d.services.items():
        if name == "unrouted":
            continue
        hosts = info["hosts"]
        expr_parts = [f"event.summary matches part '[{_pcl_escape(name)}]'"]
        if hosts:
            expr_parts.append(
                "event.source matches regex '^(" + "|".join(re.escape(h) for h in hosts[:20]) + ")$'"
            )
        suspend = [
            {
                "label": f"Flapping: {f['check']} on {f['host']}",
                "expression": (f"event.summary matches part '{_pcl_escape(f['check'])}' and "
                               f"event.source matches '{_pcl_escape(f['host'])}'"),
                "seconds": 300,
            }
            for f in flaps_by_service.get(name, [])
        ]
        services.append(Service(
            name=name, team=info["team"], escalation_policy=f"{info['team']} escalation",
            description=(f"Discovered from {info['raw_triggers']} alerts across "
                         f"{', '.join(info['sources'])}. Projected {info['projected_pages']} pages."),
            route_expression=" or ".join(expr_parts),
            suspend_rules=suspend,
        ))
    return Design(
        name=customer, teams=teams, users=[], schedules=schedules,
        escalation_policies=policies, services=services,
        notes=["Schedules read members from var.team_members; fill terraform.tfvars before apply."],
    )
