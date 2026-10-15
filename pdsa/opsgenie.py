"""Opsgenie to PagerDuty migration.

Opsgenie reaches end of support on April 5, 2027 and unmigrated data is
deleted after that date. This module pulls a full Opsgenie config (or reads
an exported bundle), converts it to the shared Design, and records every
place where the two models do not map one to one, so the SA can walk the
customer through each gap instead of discovering it in production.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .design import Design, EscalationPolicy, EscalationRule, Layer, Schedule, Service, Target

ROTATION_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800}
OG_DAYS = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
           "friday": 5, "saturday": 6, "sunday": 7}
DEFAULT_LAST_DELAY_MIN = 15


# Pull ---------------------------------------------------------------------

def _get(base: str, path: str, key: str, params: dict | None = None) -> dict:
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"GenieKey {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https host)
        return json.load(resp)


def pull(api_key: str | None = None, region: str = "us") -> dict:
    """Export teams, users, schedules, escalations and services via the REST API."""
    key = api_key or os.environ["OPSGENIE_API_KEY"]
    base = "https://api.eu.opsgenie.com" if region == "eu" else "https://api.opsgenie.com"
    users, offset = [], 0
    while True:
        page = _get(base, "/v2/users", key, {"limit": 100, "offset": offset})
        users += page.get("data", [])
        if len(page.get("data", [])) < 100:
            break
        offset += 100
    return {
        "teams": _get(base, "/v2/teams", key).get("data", []),
        "users": users,
        "schedules": _get(base, "/v2/schedules", key, {"expand": "rotation"}).get("data", []),
        "escalations": _get(base, "/v2/escalations", key).get("data", []),
        "services": _get(base, "/v1/services", key).get("data", []),
    }


# Convert ------------------------------------------------------------------

def _rfc3339(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.isoformat()


def _restrictions(tr: dict | None, notes: list[str], where: str) -> list[dict]:
    if not tr:
        return []
    kind = tr.get("type")
    if kind == "time-of-day":
        r = tr["restriction"]
        start = r["startHour"] * 60 + r["startMin"]
        end = r["endHour"] * 60 + r["endMin"]
        dur = (end - start) % (24 * 60) or 24 * 60
        return [{"type": "daily_restriction",
                 "start_time_of_day": f"{r['startHour']:02d}:{r['startMin']:02d}:00",
                 "duration_seconds": dur * 60}]
    if kind == "weekday-and-time-of-day":
        out = []
        for r in tr.get("restrictions", []):
            sd, ed = OG_DAYS[r["startDay"].lower()], OG_DAYS[r["endDay"].lower()]
            start = (sd - 1) * 1440 + r["startHour"] * 60 + r["startMin"]
            end = (ed - 1) * 1440 + r["endHour"] * 60 + r["endMin"]
            dur = (end - start) % (7 * 1440) or 7 * 1440
            out.append({"type": "weekly_restriction",
                        "start_time_of_day": f"{r['startHour']:02d}:{r['startMin']:02d}:00",
                        "start_day_of_week": sd, "duration_seconds": dur * 60})
        return out
    notes.append(f"{where}: unsupported time restriction type '{kind}', layer has no restriction.")
    return []


def convert(bundle: dict, name: str = "opsgenie migration") -> Design:
    notes: list[str] = []
    teams = sorted({t["name"] for t in bundle.get("teams", [])})
    team_by_id = {t["id"]: t["name"] for t in bundle.get("teams", [])}
    users = [{"email": u["username"], "name": u.get("fullName") or u["username"]}
             for u in bundle.get("users", [])]
    known = {u["email"] for u in users}

    schedules: list[Schedule] = []
    team_schedule: dict[str, str] = {}
    for s in bundle.get("schedules", []):
        owner = (s.get("ownerTeam") or {}).get("name", "")
        layers = []
        for rot in s.get("rotations", []):
            where = f"schedule '{s['name']}' rotation '{rot.get('name', '')}'"
            people = []
            for p in rot.get("participants", []):
                if p.get("type") == "user" and p.get("username"):
                    people.append(p["username"])
                    if p["username"] not in known:
                        users.append({"email": p["username"], "name": p["username"]})
                        known.add(p["username"])
                elif p.get("type") == "none":
                    notes.append(f"{where}: 'no one' slot dropped; PagerDuty layers have no empty turn.")
                else:
                    notes.append(f"{where}: participant type '{p.get('type')}' needs manual mapping.")
            if not people:
                notes.append(f"{where}: no user participants, layer skipped.")
                continue
            secs = ROTATION_SECONDS.get(rot.get("type", "weekly"), 604800) * int(rot.get("length", 1))
            layers.append(Layer(
                name=rot.get("name") or f"rotation {len(layers) + 1}",
                start=_rfc3339(rot["startDate"]), rotation_seconds=secs, users=people,
                restrictions=_restrictions(rot.get("timeRestriction"), notes, where),
            ))
        if not layers:
            notes.append(f"schedule '{s['name']}': nothing to migrate, skipped.")
            continue
        # Opsgenie: later rotations win. PagerDuty: higher layers win. Order is preserved.
        schedules.append(Schedule(name=s["name"], team=owner,
                                  time_zone=s.get("timezone", "UTC"), layers=layers))
        team_schedule.setdefault(owner, s["name"])

    sched_names = {s.name for s in schedules}
    policies: list[EscalationPolicy] = []
    team_policy: dict[str, str] = {}
    for e in bundle.get("escalations", []):
        owner = (e.get("ownerTeam") or {}).get("name", "")
        # Opsgenie delays are measured from alert creation; PagerDuty delays are
        # the wait before moving to the next rule. Bucket by absolute delay, then
        # convert to gaps.
        buckets: dict[int, list[Target]] = {}
        for r in e.get("rules", []):
            if r.get("condition", "if-not-acked") != "if-not-acked":
                notes.append(f"escalation '{e['name']}': condition '{r['condition']}' "
                             "approximated as if not acknowledged.")
            d = r.get("delay", {})
            mins = int(d.get("timeAmount", 0)) * (60 if d.get("timeUnit") == "hours" else 1)
            rec = r.get("recipient", {})
            kind = rec.get("type")
            if kind == "schedule" and rec.get("name") in sched_names:
                if r.get("notifyType", "default") not in ("default", "all"):
                    notes.append(f"escalation '{e['name']}': notify '{r['notifyType']}' on call "
                                 "has no direct equivalent; notifying current on call.")
                buckets.setdefault(mins, []).append(Target("schedule", rec["name"]))
            elif kind == "user" and rec.get("username"):
                buckets.setdefault(mins, []).append(Target("user", rec["username"]))
                if rec["username"] not in known:
                    users.append({"email": rec["username"], "name": rec["username"]})
                    known.add(rec["username"])
            elif kind == "team":
                tname = rec.get("name") or team_by_id.get(rec.get("id", ""), "")
                if tname in team_schedule:
                    buckets.setdefault(mins, []).append(Target("schedule", team_schedule[tname]))
                    notes.append(f"escalation '{e['name']}': team '{tname}' mapped to its "
                                 f"schedule '{team_schedule[tname]}'.")
                else:
                    notes.append(f"escalation '{e['name']}': team recipient '{tname}' has no schedule.")
            else:
                notes.append(f"escalation '{e['name']}': recipient {rec} not mapped.")
        if not buckets:
            notes.append(f"escalation '{e['name']}': no mappable recipients, skipped.")
            continue
        times = sorted(buckets)
        rules = []
        for i, t in enumerate(times):
            gap = (times[i + 1] - t) if i + 1 < len(times) else DEFAULT_LAST_DELAY_MIN
            rules.append(EscalationRule(delay_min=max(1, gap), targets=buckets[t]))
        if times[0] > 0:
            notes.append(f"escalation '{e['name']}': first rule waited {times[0]} min in Opsgenie; "
                         "PagerDuty notifies the first rule immediately.")
        loops = int((e.get("repeat") or {}).get("count", 0)) if (e.get("repeat") or {}).get("waitInterval") else 0
        policies.append(EscalationPolicy(name=e["name"], team=owner, rules=rules,
                                         num_loops=min(max(loops, 0), 9)))
        team_policy.setdefault(owner, e["name"])

    services: list[Service] = []
    for svc in bundle.get("services", []):
        tname = team_by_id.get(svc.get("teamId", ""), "")
        policy = team_policy.get(tname, "")
        if not policy:
            notes.append(f"service '{svc['name']}': owning team has no escalation policy; "
                         "uses var.default_escalation_policy_id.")
        services.append(Service(name=svc["name"], team=tname, escalation_policy=policy,
                                description=svc.get("description", ""),
                                intelligent_grouping=True))

    notes.append("Opsgenie alert policies, integrations, and heartbeats are inventoried, "
                 "not converted: rebuild them as Event Orchestration rules and integrations.")
    return Design(name=name, teams=teams, users=users, schedules=schedules,
                  escalation_policies=policies, services=services, create_users=True, notes=notes)


def load_bundle(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
