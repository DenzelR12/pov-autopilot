"""Render a Design as Terraform for the official PagerDuty provider.

Output is plain, reviewable HCL a customer's platform team can own after
the POV: no modules to learn, one resource per real object.
"""

from __future__ import annotations

import json
from pathlib import Path

from .design import Design, slug

PROVIDER_VERSION = "~> 3.15"


def q(text: str) -> str:
    """HCL string literal. JSON escaping is valid HCL, plus template escapes."""
    return json.dumps(text).replace("${", "$${").replace("%{", "%%{")


def _user_ref(email: str, design: Design) -> str:
    kind = "pagerduty_user" if design.create_users else "data.pagerduty_user"
    return f"{kind}.{slug(email)}.id"


def render(design: Design) -> dict[str, str]:
    main: list[str] = []
    w = main.append

    w("terraform {")
    w("  required_version = \">= 1.5\"")
    w("  required_providers {")
    w(f"    pagerduty = {{\n      source  = \"PagerDuty/pagerduty\"\n      version = \"{PROVIDER_VERSION}\"\n    }}")
    w("  }")
    w("}\n")
    w("provider \"pagerduty\" {\n  token = var.pagerduty_token\n}\n")

    # Teams
    for team in design.teams:
        w(f"resource \"pagerduty_team\" \"{slug(team)}\" {{\n  name = {q(team)}\n}}\n")

    # People
    emails = sorted({u["email"] for u in design.users})
    names = {u["email"]: u.get("name") or u["email"].split("@")[0] for u in design.users}
    for email in emails:
        if design.create_users:
            w(f"resource \"pagerduty_user\" \"{slug(email)}\" {{\n"
              f"  name  = {q(names[email])}\n  email = {q(email)}\n}}\n")
        else:
            w(f"data \"pagerduty_user\" \"{slug(email)}\" {{\n  email = {q(email)}\n}}\n")

    uses_team_var = any(l.team_var for s in design.schedules for l in s.layers)
    if uses_team_var:
        w("data \"pagerduty_user\" \"member\" {\n"
          "  for_each = toset(flatten(values(var.team_members)))\n"
          "  email    = each.value\n}\n")

    # Schedules
    for s in design.schedules:
        w(f"resource \"pagerduty_schedule\" \"{slug(s.name)}\" {{")
        w(f"  name      = {q(s.name)}")
        w(f"  time_zone = {q(s.time_zone)}")
        if s.team in design.teams:
            w(f"  teams     = [pagerduty_team.{slug(s.team)}.id]")
        for layer in s.layers:
            if layer.team_var:
                users = (f"[for e in lookup(var.team_members, {q(layer.team_var)}, []) : "
                         "data.pagerduty_user.member[e].id]")
            else:
                users = "[" + ", ".join(_user_ref(e, design) for e in layer.users) + "]"
            w("  layer {")
            w(f"    name                         = {q(layer.name)}")
            w(f"    start                        = {q(layer.start)}")
            w(f"    rotation_virtual_start       = {q(layer.start)}")
            w(f"    rotation_turn_length_seconds = {layer.rotation_seconds}")
            w(f"    users                        = {users}")
            for r in layer.restrictions:
                w("    restriction {")
                w(f"      type              = {q(r['type'])}")
                w(f"      start_time_of_day = {q(r['start_time_of_day'])}")
                w(f"      duration_seconds  = {int(r['duration_seconds'])}")
                if r.get("start_day_of_week"):
                    w(f"      start_day_of_week = {int(r['start_day_of_week'])}")
                w("    }")
            w("  }")
        w("}\n")

    sched_names = {s.name for s in design.schedules}

    # Escalation policies
    for p in design.escalation_policies:
        w(f"resource \"pagerduty_escalation_policy\" \"{slug(p.name)}\" {{")
        w(f"  name      = {q(p.name)}")
        w(f"  num_loops = {p.num_loops}")
        if p.team in design.teams:
            w(f"  teams     = [pagerduty_team.{slug(p.team)}.id]")
        for r in p.rules:
            w("  rule {")
            w(f"    escalation_delay_in_minutes = {max(1, r.delay_min)}")
            for t in r.targets:
                if t.kind == "schedule" and t.ref in sched_names:
                    w(f"    target {{\n      type = \"schedule_reference\"\n"
                      f"      id   = pagerduty_schedule.{slug(t.ref)}.id\n    }}")
                elif t.kind == "user":
                    w(f"    target {{\n      type = \"user_reference\"\n"
                      f"      id   = {_user_ref(t.ref, design)}\n    }}")
            w("  }")
        w("}\n")

    policy_names = {p.name for p in design.escalation_policies}

    # Services and per service orchestration
    for svc in design.services:
        sid = slug(svc.name)
        ep = svc.escalation_policy if svc.escalation_policy in policy_names else None
        w(f"resource \"pagerduty_service\" \"{sid}\" {{")
        w(f"  name              = {q(svc.name)}")
        if svc.description:
            w(f"  description       = {q(svc.description)}")
        if ep:
            w(f"  escalation_policy = pagerduty_escalation_policy.{slug(ep)}.id")
        else:
            w("  escalation_policy = var.default_escalation_policy_id")
        w("  alert_creation    = \"create_alerts_and_incidents\"")
        if svc.severity_based_urgency:
            w("  incident_urgency_rule {\n    type    = \"constant\"\n    urgency = \"severity_based\"\n  }")
        if svc.intelligent_grouping:
            w("  alert_grouping_parameters {\n    type = \"intelligent\"\n  }")
        w("}\n")

        if svc.suspend_rules:
            w(f"resource \"pagerduty_event_orchestration_service\" \"{sid}\" {{")
            w(f"  service                                = pagerduty_service.{sid}.id")
            w("  enable_event_orchestration_for_service = true")
            w("  set {\n    id = \"start\"")
            for r in svc.suspend_rules:
                w("    rule {")
                w(f"      label = {q(r['label'])}")
                w(f"      condition {{\n        expression = {q(r['expression'])}\n      }}")
                w("      actions {")
                w("        # Hold the alert; if the check recovers inside the window, nobody is paged.")
                w(f"        suspend = {int(r['seconds'])}")
                w("      }")
                w("    }")
            w("  }")
            w("  catch_all {\n    actions {}\n  }")
            w("}\n")

    # Global orchestration: one routing key for every source, rules pick the service.
    routed = [s for s in design.services if s.route_expression]
    if routed:
        oid = slug(f"{design.name} global")
        w(f"resource \"pagerduty_event_orchestration\" \"{oid}\" {{")
        w(f"  name = {q(design.name + ' global orchestration')}")
        w("}\n")
        w(f"resource \"pagerduty_event_orchestration_router\" \"{oid}\" {{")
        w(f"  event_orchestration = pagerduty_event_orchestration.{oid}.id")
        w("  set {\n    id = \"start\"")
        for s in routed:
            w("    rule {")
            w(f"      label = {q('Route to ' + s.name)}")
            w(f"      condition {{\n        expression = {q(s.route_expression)}\n      }}")
            w(f"      actions {{\n        route_to = pagerduty_service.{slug(s.name)}.id\n      }}")
            w("    }")
        w("  }")
        w("  catch_all {\n    actions {\n      route_to = \"unrouted\"\n    }\n  }")
        w("}\n")

    variables = [
        "variable \"pagerduty_token\" {\n  description = \"PagerDuty REST API token\"\n"
        "  type        = string\n  sensitive   = true\n}\n",
    ]
    if uses_team_var:
        variables.append(
            "variable \"team_members\" {\n"
            "  description = \"Team name to list of on call member emails (users must already exist)\"\n"
            "  type        = map(list(string))\n  default     = {}\n}\n"
        )
    if any(s.escalation_policy not in policy_names for s in design.services):
        variables.append(
            "variable \"default_escalation_policy_id\" {\n"
            "  description = \"Escalation policy for services whose source policy could not be mapped\"\n"
            "  type        = string\n}\n"
        )

    outputs = []
    if routed:
        oid = slug(f"{design.name} global")
        outputs.append(
            "output \"global_routing_key\" {\n"
            "  description = \"Point every monitoring tool at this Events API v2 routing key\"\n"
            f"  value       = pagerduty_event_orchestration.{oid}.integration[0].parameters[0].routing_key\n"
            "  sensitive   = true\n}\n"
        )
    width = max((len(q(s.name)) for s in design.services), default=0)
    outputs.append(
        "output \"service_ids\" {\n  value = {\n"
        + "".join(f"    {q(s.name).ljust(width)} = pagerduty_service.{slug(s.name)}.id\n"
                  for s in design.services)
        + "  }\n}\n"
    )

    tfvars = ["pagerduty_token = \"replace-me\"\n"]
    if uses_team_var:
        tfvars.append("team_members = {\n" + "".join(
            f"  {q(t)} = [\"oncall-{slug(t)}@example.com\"]\n" for t in design.teams) + "}\n")

    return {
        "main.tf": "\n".join(main),
        "variables.tf": "\n".join(variables),
        "outputs.tf": "\n".join(outputs),
        "terraform.tfvars.example": "".join(tfvars),
    }


def write(design: Design, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in render(design).items():
        p = out / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths
