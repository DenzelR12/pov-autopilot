"""Alert loading and normalization.

Every downstream step works on one normalized shape so that an export from
Datadog, Prometheus Alertmanager, CloudWatch, Nagios, or an Opsgenie alert
dump can be analyzed the same way. Unknown columns are kept in ``extra``.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SEVERITIES = ("critical", "error", "warning", "info")

# Column aliases seen in common monitoring exports.
ALIASES = {
    "timestamp": ("timestamp", "time", "created_at", "createdat", "date", "fired_at", "startsat"),
    "source": ("source", "tool", "integration", "monitor_source", "origin"),
    "host": ("host", "hostname", "instance", "resource", "entity"),
    "check": ("check", "alertname", "monitor", "monitor_name", "rule", "metric"),
    "summary": ("summary", "message", "title", "description", "alias"),
    "severity": ("severity", "priority", "level", "urgency"),
    "status": ("status", "state", "event_action", "action"),
    "service": ("service", "app", "application", "component", "team_service"),
}

SEVERITY_MAP = {
    "p1": "critical", "p2": "error", "p3": "warning", "p4": "info", "p5": "info",
    "crit": "critical", "critical": "critical", "sev1": "critical", "high": "error",
    "err": "error", "error": "error", "sev2": "error", "major": "error",
    "warn": "warning", "warning": "warning", "sev3": "warning", "medium": "warning", "minor": "warning",
    "info": "info", "informational": "info", "low": "info", "ok": "info",
}

RESOLVE_WORDS = {"resolve", "resolved", "ok", "recovered", "closed", "close"}

_NOISE_PATTERNS = [
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|ms|s|gb|mb|kb)?\b", re.I), "<n>"),
    (re.compile(r"\s+"), " "),
]


@dataclass
class Alert:
    timestamp: datetime
    source: str
    host: str
    check: str
    summary: str
    severity: str
    status: str  # "trigger" or "resolve"
    service: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable identity of 'the same alert', independent of changing numbers."""
        # Check plus host is what a well configured dedup_key encodes. The summary
        # is left out because tools put live values in it ("disk at 91%").
        # Host stays literal: web-01 and web-02 are different alerts.
        return f"{normalize_text(self.check or self.summary)}|{self.host.lower()}"

    @property
    def check_family(self) -> str:
        """The same check across all hosts, used for storm and grouping detection."""
        return normalize_text(self.check or self.summary)


def normalize_text(text: str) -> str:
    text = text.lower()
    for pattern, repl in _NOISE_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


def parse_time(value: str) -> datetime:
    value = value.strip()
    if re.fullmatch(r"\d{10}(\.\d+)?", value):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if re.fullmatch(r"\d{13}", value):
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_severity(value: str) -> str:
    return SEVERITY_MAP.get(value.strip().lower(), "warning")


def normalize_status(value: str) -> str:
    return "resolve" if value.strip().lower() in RESOLVE_WORDS else "trigger"


def _resolve_columns(header: Iterable[str]) -> dict[str, str]:
    lower = {h.lower().strip(): h for h in header}
    mapping = {}
    for canonical, options in ALIASES.items():
        for opt in options:
            if opt in lower:
                mapping[canonical] = lower[opt]
                break
    if "timestamp" not in mapping:
        raise ValueError("alert export needs a timestamp column (e.g. 'timestamp', 'created_at')")
    if "check" not in mapping and "summary" not in mapping:
        raise ValueError("alert export needs a 'check' or 'summary' column")
    return mapping


def load_csv(path: str | Path) -> list[Alert]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = _resolve_columns(reader.fieldnames or [])
        used = set(cols.values())
        alerts = []
        for row in reader:
            get = lambda k, d="": (row.get(cols[k]) or d).strip() if k in cols else d  # noqa: E731
            alerts.append(
                Alert(
                    timestamp=parse_time(get("timestamp")),
                    source=get("source", "unknown"),
                    host=get("host"),
                    check=get("check") or get("summary"),
                    summary=get("summary") or get("check"),
                    severity=normalize_severity(get("severity", "warning")),
                    status=normalize_status(get("status", "trigger")),
                    service=get("service"),
                    extra={k: v for k, v in row.items() if k not in used},
                )
            )
    alerts.sort(key=lambda a: a.timestamp)
    return alerts


def write_csv(alerts: list[Alert], path: str | Path) -> None:
    cols = ["timestamp", "source", "host", "check", "summary", "severity", "status", "service"]
    extra_cols = sorted({k for a in alerts for k in a.extra})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols + extra_cols)
        for a in alerts:
            w.writerow([a.timestamp.isoformat(), a.source, a.host, a.check, a.summary,
                        a.severity, a.status, a.service] + [a.extra.get(k, "") for k in extra_cols])
