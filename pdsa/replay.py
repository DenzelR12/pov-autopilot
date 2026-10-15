"""Proof of value replay.

Replays the customer's own alert history (or a slice of it, such as the
worst storm) into a PagerDuty account through the Events API v2, time
compressed, so the champion watches their real noise collapse into a
handful of incidents live in the POV.

Dry run by default: nothing is sent unless --send is passed.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Iterator

from .alerts import Alert

EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


def dedup_key(a: Alert) -> str:
    return "pdsa-" + hashlib.sha1(a.fingerprint.encode()).hexdigest()[:20]


def to_event(a: Alert, routing_key: str) -> dict:
    if a.status == "resolve":
        return {"routing_key": routing_key, "event_action": "resolve", "dedup_key": dedup_key(a)}
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup_key(a),
        "payload": {
            "summary": a.summary[:1024],
            "source": a.host or a.source,
            "severity": a.severity,
            "timestamp": a.timestamp.isoformat(),
            "component": a.check[:255],
            "group": a.service,
            "class": a.source,
            "custom_details": {"original_timestamp": a.timestamp.isoformat(), **a.extra},
        },
    }


def select_window(alerts: list[Alert], start: datetime, minutes: int) -> list[Alert]:
    end = start + timedelta(minutes=minutes)
    return [a for a in alerts if start <= a.timestamp <= end]


def schedule(alerts: list[Alert], speed: float, min_gap: float) -> Iterator[tuple[float, Alert]]:
    """Yield (seconds to wait before sending, alert) with history compressed by `speed`."""
    prev = None
    for a in alerts:
        wait = 0.0 if prev is None else (a.timestamp - prev).total_seconds() / speed
        prev = a.timestamp
        yield max(wait, min_gap), a


def send(event: dict, timeout: float = 10.0) -> int:
    req = urllib.request.Request(
        EVENTS_URL, data=json.dumps(event).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return resp.status


def replay(alerts: list[Alert], routing_key: str, speed: float = 60.0, min_gap: float = 0.5,
           live: bool = False, out=print) -> int:
    sent = 0
    for wait, a in schedule(alerts, speed, min_gap):
        ev = to_event(a, routing_key)
        if live:
            time.sleep(wait)
            status = send(ev)
            out(f"{status} {ev['event_action']:7} {a.summary[:90]}")
        else:
            safe = {**ev, "routing_key": "<redacted>"}
            out(json.dumps(safe))
        sent += 1
    return sent
