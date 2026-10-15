"""Discovery: turn a raw alert export into an operational picture.

Answers the questions an SA asks in the first two calls, from data instead
of opinion: how noisy is this, where does the noise come from, which
services exist, who owns them, and how many pages would they actually get
with dedup, suppression, and grouping in place.

All reduction numbers are *estimates* from simple, explainable rules. They
are deliberately conservative proxies for the platform's own grouping, so
the POV can only beat them, never fall short of them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

from .alerts import Alert

_BRACKET = re.compile(r"\[([a-z0-9][\w\-]*)\]", re.I)
_HOST_PREFIX = re.compile(r"^([a-z]+)", re.I)


@dataclass
class Settings:
    group_window_min: int = 5
    flap_max_open_min: int = 10
    flap_min_cycles: int = 5
    storm_min_services: int = 3
    auto_resolve_hours: float = 4.0  # an alert with no resolve event is treated as closed after this
    business_tz: str = "America/New_York"
    business_hours: tuple[int, int] = (8, 18)


@dataclass
class Discovery:
    window: dict
    funnel: list[tuple[str, int]]
    by_source: dict
    by_severity: dict
    services: dict
    flapping: list[dict]
    storms: list[dict]
    off_hours_share: float
    top_noisy: list[dict]
    recommendations: list[str] = field(default_factory=list)

    @property
    def raw(self) -> int:
        return self.funnel[0][1]

    @property
    def pages(self) -> int:
        return self.funnel[-1][1]

    @property
    def reduction(self) -> float:
        return 0.0 if not self.raw else 1 - self.pages / self.raw

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "funnel": [{"stage": s, "count": c} for s, c in self.funnel],
            "reduction": round(self.reduction, 4),
            "by_source": self.by_source,
            "by_severity": self.by_severity,
            "services": self.services,
            "flapping": self.flapping,
            "storms": self.storms,
            "off_hours_share": round(self.off_hours_share, 4),
            "top_noisy": self.top_noisy,
            "recommendations": self.recommendations,
        }


def infer_service(a: Alert, host_map: dict[str, str]) -> str:
    if a.service:
        return a.service
    m = _BRACKET.search(a.summary)
    if m:
        return m.group(1).lower()
    if a.host in host_map:
        return host_map[a.host]
    m = _HOST_PREFIX.match(a.host or "")
    return m.group(1).lower() if m else "unrouted"


def _learn_host_map(alerts: list[Alert]) -> dict[str, str]:
    """Hosts that ever appear with an explicit service inherit it."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for a in alerts:
        svc = a.service or (m.group(1).lower() if (m := _BRACKET.search(a.summary)) else "")
        if svc and a.host:
            votes[a.host][svc] += 1
    return {h: c.most_common(1)[0][0] for h, c in votes.items()}


def analyze(alerts: list[Alert], settings: Settings | None = None) -> Discovery:
    s = settings or Settings()
    if not alerts:
        raise ValueError("no alerts to analyze")
    host_map = _learn_host_map(alerts)
    for a in alerts:
        a.service = infer_service(a, host_map)

    triggers = [a for a in alerts if a.status == "trigger"]

    # Stage 1: dedup. A trigger for a fingerprint that is already open would
    # carry the same dedup_key and append to the open alert, not page again.
    open_since: dict[str, Alert] = {}
    openings: list[Alert] = []
    cycles: dict[str, list[float]] = defaultdict(list)
    stale = timedelta(hours=s.auto_resolve_hours)
    for a in alerts:
        fp = a.fingerprint
        if a.status == "trigger":
            if fp in open_since and a.timestamp - open_since[fp].timestamp > stale:
                del open_since[fp]  # silently timed out; this trigger opens a new alert
            if fp not in open_since:
                open_since[fp] = a
                openings.append(a)
        elif fp in open_since:
            opened = open_since.pop(fp)
            cycles[fp].append((a.timestamp - opened.timestamp).total_seconds() / 60)

    # Stage 2: flap suppression for fingerprints that open and close fast, often.
    flapping_fps = {
        fp for fp, durs in cycles.items()
        if sum(1 for d in durs if d <= s.flap_max_open_min) >= s.flap_min_cycles
    }
    sample_by_fp = {a.fingerprint: a for a in openings}
    flapping = sorted(
        (
            {
                "fingerprint": fp,
                "service": sample_by_fp[fp].service,
                "host": sample_by_fp[fp].host,
                "check": sample_by_fp[fp].check,
                "cycles": len(cycles[fp]),
                "median_open_min": round(sorted(cycles[fp])[len(cycles[fp]) // 2], 1),
            }
            for fp in flapping_fps
        ),
        key=lambda d: -d["cycles"],
    )
    after_flap = [a for a in openings if a.fingerprint not in flapping_fps]

    # Stage 3: per service time window grouping (proxy for alert grouping).
    window = timedelta(minutes=s.group_window_min)
    incidents: list[list[Alert]] = []
    last_by_service: dict[str, list[Alert]] = {}
    for a in after_flap:
        cur = last_by_service.get(a.service)
        if cur and a.timestamp - cur[0].timestamp <= window:  # fixed window from first alert, no chaining
            cur.append(a)
        else:
            cur = [a]
            incidents.append(cur)
            last_by_service[a.service] = cur

    # Stage 4: urgency. Only incidents with an error or critical alert page.
    paging = [inc for inc in incidents if any(x.severity in ("critical", "error") for x in inc)]

    funnel = [
        ("Raw alert triggers", len(triggers)),
        ("After dedup (one open alert per fingerprint)", len(openings)),
        ("After flap suppression", len(after_flap)),
        (f"Grouped into incidents ({s.group_window_min} min window per service)", len(incidents)),
        ("High urgency pages (error or critical)", len(paging)),
    ]

    # Cross service storms: likely one root cause, a case for service
    # dependencies and global orchestration.
    storms = []
    i = 0
    while i < len(after_flap):
        j = i
        while j + 1 < len(after_flap) and after_flap[j + 1].timestamp - after_flap[j].timestamp <= window:
            j += 1
        chunk = after_flap[i: j + 1]
        svcs = Counter(x.service for x in chunk)
        if len(svcs) >= s.storm_min_services:
            first = min(chunk, key=lambda x: x.timestamp)
            crit = [x for x in chunk if x.severity == "critical"]
            storms.append({
                "start": first.timestamp.isoformat(),
                "alerts": len(chunk),
                "services": dict(svcs.most_common()),
                "likely_root": (min(crit, key=lambda x: x.timestamp) if crit else first).service,
            })
        i = j + 1

    tz = ZoneInfo(s.business_tz)
    lo, hi = s.business_hours

    def off_hours(a: Alert) -> bool:
        local = a.timestamp.astimezone(tz)
        return local.weekday() >= 5 or not (lo <= local.hour < hi)

    paging_heads = [inc[0] for inc in paging]
    off_share = sum(off_hours(a) for a in paging_heads) / len(paging_heads) if paging_heads else 0.0

    services: dict[str, dict] = {}
    for a in alerts:
        d = services.setdefault(a.service, {"hosts": set(), "sources": set(), "teams": Counter(),
                                            "triggers": 0, "severities": Counter()})
        if a.host:
            d["hosts"].add(a.host)
        d["sources"].add(a.source)
        if a.extra.get("team"):
            d["teams"][a.extra["team"]] += 1
        if a.status == "trigger":
            d["triggers"] += 1
            d["severities"][a.severity] += 1
    incidents_by_service = Counter(inc[0].service for inc in incidents)
    pages_by_service = Counter(inc[0].service for inc in paging)
    services_out = {
        name: {
            "team": d["teams"].most_common(1)[0][0] if d["teams"] else "unassigned",
            "hosts": sorted(d["hosts"]),
            "sources": sorted(d["sources"]),
            "raw_triggers": d["triggers"],
            "projected_incidents": incidents_by_service.get(name, 0),
            "projected_pages": pages_by_service.get(name, 0),
            "severities": dict(d["severities"]),
        }
        for name, d in sorted(services.items())
    }

    noisy = Counter(a.fingerprint for a in triggers)
    top_noisy = [
        {"fingerprint": fp, "service": sample_by_fp[fp].service, "check": sample_by_fp[fp].check,
         "host": sample_by_fp[fp].host, "triggers": n}
        for fp, n in noisy.most_common(10)
    ]

    disc = Discovery(
        window={
            "start": alerts[0].timestamp.isoformat(),
            "end": alerts[-1].timestamp.isoformat(),
            "days": round((alerts[-1].timestamp - alerts[0].timestamp).total_seconds() / 86400, 1),
        },
        funnel=funnel,
        by_source=dict(Counter(a.source for a in triggers).most_common()),
        by_severity=dict(Counter(a.severity for a in triggers).most_common()),
        services=services_out,
        flapping=flapping,
        storms=storms,
        off_hours_share=off_share,
        top_noisy=top_noisy,
    )
    disc.recommendations = recommend(disc)
    return disc


def recommend(d: Discovery) -> list[str]:
    recs = []
    raw, dedup = d.funnel[0][1], d.funnel[1][1]
    if raw and (raw - dedup) / raw > 0.1:
        recs.append(
            f"Send a stable dedup_key from every source: {raw - dedup:,} of {raw:,} triggers "
            f"({(raw - dedup) / raw:.0%}) re-fire for an alert that is already open."
        )
    if d.flapping:
        worst = d.flapping[0]
        recs.append(
            f"Suppress or delay {len(d.flapping)} flapping checks with Event Orchestration. "
            f"Worst: '{worst['check']}' on {worst['host']} cycled {worst['cycles']} times "
            f"(median {worst['median_open_min']} min open)."
        )
    if d.storms:
        roots = Counter(st["likely_root"] for st in d.storms)
        recs.append(
            f"Model service dependencies: {len(d.storms)} cross service storms detected; "
            f"most common likely root is '{roots.most_common(1)[0][0]}'. "
            "Turn on Intelligent Alert Grouping and route the root service first."
        )
    low = d.by_severity.get("info", 0) + d.by_severity.get("warning", 0)
    if raw and low / raw > 0.3:
        recs.append(
            f"Make warning and info alerts low urgency: {low / raw:.0%} of triggers are below "
            "error severity and should not wake anyone."
        )
    if d.off_hours_share > 0.4:
        recs.append(
            f"{d.off_hours_share:.0%} of projected pages land outside business hours: "
            "review on call load and consider support hours based urgency rules."
        )
    unowned = [n for n, s in d.services.items() if s["team"] == "unassigned"]
    if unowned:
        recs.append(f"Assign owners for {len(unowned)} services with no team: {', '.join(unowned[:5])}.")
    return recs
