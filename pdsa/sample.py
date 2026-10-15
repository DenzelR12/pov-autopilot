"""Synthetic but realistic alert history for demos and tests.

Models the failure patterns an SA sees in almost every discovery call:
flapping checks, re-fire storms with no dedup, cascading outages across
services, and low value info alerts that still page someone.
Deterministic for a given seed.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .alerts import Alert

TOPOLOGY = {
    # service: (team, hosts, monitoring source, checks)
    "checkout-api": ("commerce", ["chk-01", "chk-02", "chk-03"], "datadog",
                     ["http 5xx rate high", "p99 latency above slo", "pod restart loop"]),
    "payments": ("commerce", ["pay-01", "pay-02"], "datadog",
                 ["gateway timeout rate", "payment auth failures", "queue depth high"]),
    "inventory-db": ("data", ["invdb-01", "invdb-02"], "cloudwatch",
                     ["replica lag high", "cpu utilization high", "connection pool exhausted"]),
    "search": ("discovery", ["srch-01", "srch-02", "srch-03"], "prometheus",
               ["elasticsearch heap pressure", "query latency high", "shard unassigned"]),
    "identity": ("platform", ["auth-01", "auth-02"], "prometheus",
                 ["token issuance errors", "ldap sync failed", "cert expires soon"]),
    "edge": ("platform", ["lb-01", "lb-02"], "cloudwatch",
             ["unhealthy targets", "tls handshake errors", "disk usage above 85"]),
}

ROOT_CHECK = {"inventory-db": "connection pool exhausted", "identity": "token issuance errors",
              "edge": "unhealthy targets"}

# Outage blast radius: root service -> services that alert downstream.
CASCADES = {
    "inventory-db": ["checkout-api", "payments", "search"],
    "identity": ["checkout-api", "payments"],
    "edge": ["checkout-api", "search", "identity"],
}


def _alert(ts, service, host, check, severity, status="trigger", detail=""):
    team, _, source, _ = TOPOLOGY[service]
    summary = f"[{service}] {check} on {host}{detail}"
    return Alert(timestamp=ts, source=source, host=host, check=check, summary=summary,
                 severity=severity, status=status, service="",
                 extra={"team": team})


def generate(days: int = 30, seed: int = 7, start: datetime | None = None) -> list[Alert]:
    rng = random.Random(seed)
    start = start or datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=days)
    out: list[Alert] = []

    # 1. Flapping checks: a few host checks bounce trigger/resolve all month.
    flappers = [("edge", "lb-02", "disk usage above 85"),
                ("search", "srch-03", "elasticsearch heap pressure"),
                ("identity", "auth-02", "cert expires soon")]
    for service, host, check in flappers:
        t = start + timedelta(minutes=rng.randint(0, 300))
        while t < end:
            out.append(_alert(t, service, host, check, "warning",
                              detail=f" ({rng.randint(84, 93)}%)"))
            t += timedelta(minutes=rng.randint(2, 9))
            out.append(_alert(t, service, host, check, "warning", status="resolve"))
            t += timedelta(minutes=rng.randint(20, 240))

    # 2. Background single alerts, some of which re-fire every 5 minutes
    #    because the source tool has no dedup key.
    t = start
    while t < end:
        t += timedelta(minutes=rng.expovariate(1 / 55))
        service = rng.choice(list(TOPOLOGY))
        _, hosts, _, checks = TOPOLOGY[service]
        host, check = rng.choice(hosts), rng.choice(checks)
        sev = rng.choices(["critical", "error", "warning", "info"], [1, 3, 5, 4])[0]
        refires = rng.choice([0, 0, 0, 2, 4, 7])
        for i in range(refires + 1):
            out.append(_alert(t + timedelta(minutes=5 * i), service, host, check, sev,
                              detail=f" value={rng.randint(1, 999)}"))
        out.append(_alert(t + timedelta(minutes=5 * refires + rng.randint(3, 40)),
                          service, host, check, sev, status="resolve"))

    # 3. Cascading outages: one root failure, dozens of downstream alerts
    #    across teams within a few minutes. This is where grouping pays off.
    for _ in range(max(1, days // 4)):
        root = rng.choice(list(CASCADES))
        t0 = start + timedelta(minutes=rng.randint(0, days * 24 * 60 - 120))
        _, hosts, _, checks = TOPOLOGY[root]
        root_check = ROOT_CHECK[root]
        for h in hosts:
            out.append(_alert(t0 + timedelta(seconds=rng.randint(0, 40)), root, h,
                              root_check, "critical"))
        recovery = t0 + timedelta(minutes=rng.randint(25, 70))
        for dep in CASCADES[root]:
            _, dhosts, _, dchecks = TOPOLOGY[dep]
            for h in dhosts:
                for c in dchecks[:2]:
                    for k in range(rng.randint(1, 3)):
                        out.append(_alert(t0 + timedelta(seconds=rng.randint(30, 420) + 60 * k),
                                          dep, h, c, "error"))
                    out.append(_alert(recovery + timedelta(minutes=rng.randint(1, 6)), dep, h, c,
                                      "error", status="resolve"))
        for h in hosts:
            out.append(_alert(recovery, root, h, root_check, "critical", status="resolve"))

    out = [a for a in out if a.timestamp < end]
    out.sort(key=lambda a: a.timestamp)
    return out
