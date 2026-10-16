"""Readers for the monitoring tools prospects actually send.

Each reader turns one tool's native export into the normalized Alert shape,
so discovery, the business case and replay work the same on any of them.
`load()` detects the format from the file itself; nobody has to say which
tool it came from.

Supported exports
  New Relic    NrAiIncident events from NRQL, CSV or JSON (conditionName, event, priority)
  Nagios       nagios.log (also Icinga 1): NOTIFICATION lines, else HARD ALERT lines
  Zabbix       Monitoring > Problems CSV export, or event.get JSON from the API
  Splunk       search or triggered alert export with _time (ITSI notable events too)
  Zenoss       Events console CSV export (Count occurrences expand into triggers)
  ServiceNow   Event Management em_event or em_alert list export
  Generic      any CSV with a timestamp and a check or summary column (Datadog,
               CloudWatch, Prometheus Alertmanager, Opsgenie alert dumps)

Timestamps without a time zone are read as UTC. Ask the prospect which zone
their export uses before quoting off hours numbers.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .alerts import Alert, load_csv, normalize_severity, parse_time

FORMATS = ("newrelic", "nagios", "zabbix", "splunk", "zenoss", "servicenow", "generic")


# Shared helpers ---------------------------------------------------------------

def _time(value) -> datetime:
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})[ T](\d{2}:\d{2}:\d{2})", s)
    if m:
        s = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})[ T](\d{1,2}:\d{2}(?::\d{2})?)\s*(AM|PM)?", s, re.I)
    if m:
        hms = m.group(4) if m.group(4).count(":") == 2 else m.group(4) + ":00"
        dt = datetime.strptime(f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d} {hms}",
                               "%Y-%m-%d %H:%M:%S")
        if m.group(5):
            hour = dt.hour % 12 + (12 if m.group(5).upper() == "PM" else 0)
            dt = dt.replace(hour=hour)
        return dt.replace(tzinfo=timezone.utc)
    return parse_time(s)


def _rows(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


def _pick(row: dict, *names: str, default: str = "") -> str:
    for n in names:
        if row.get(n):
            return row[n]
    return default


def _header(text: str) -> set[str]:
    first = text.splitlines()[0] if text.strip() else ""
    return {h.strip().strip('"').lower() for h in next(csv.reader([first]), [])}


# New Relic ------------------------------------------------------------------------

NR_PRIORITY = {"critical": "critical", "high": "error", "medium": "warning", "low": "info"}


def read_newrelic(text: str) -> list[Alert]:
    if text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):  # NerdGraph style {"results": [...]} or {"data": {...}}
            data = data.get("results") or data.get("events") or data.get("data", {}).get("results", [])
        rows = [{k.lower(): ("" if v is None else str(v)) for k, v in r.items()} for r in data]
    else:
        rows = _rows(text)
    out = []
    for r in rows:
        event = _pick(r, "event", "state", default="open").lower()
        out.append(Alert(
            timestamp=_time(_pick(r, "timestamp", "opentime", "openedat")),
            source="newrelic",
            host=_pick(r, "entity.name", "targetname", "entityname", "entity.guid"),
            check=_pick(r, "conditionname", "condition_name", "title"),
            summary=_pick(r, "title", "conditionname"),
            severity=NR_PRIORITY.get(_pick(r, "priority").lower(), "warning"),
            status="resolve" if event in ("close", "closed", "resolve") else "trigger",
            service=_pick(r, "policyname", "policy_name"),
            extra={"incident_id": _pick(r, "incidentid", "incident_id")},
        ))
    return out


# Nagios / Icinga log -----------------------------------------------------------------

_NAGIOS = re.compile(r"^\[(\d{10})\]\s+(SERVICE|HOST) (ALERT|NOTIFICATION):\s*(.*)$")
NAGIOS_SEV = {"CRITICAL": "critical", "DOWN": "critical", "UNREACHABLE": "critical",
              "WARNING": "warning", "UNKNOWN": "warning"}


def read_nagios(text: str) -> list[Alert]:
    lines = [_NAGIOS.match(ln.strip()) for ln in text.splitlines()]
    lines = [m for m in lines if m]
    # Notifications are what actually paged someone. Use them when the log has them;
    # otherwise HARD state alerts (SOFT states are retries, not alerts).
    use_notifications = any(m.group(3) == "NOTIFICATION" for m in lines)
    out = []
    for m in lines:
        ts, kind, typ, rest = m.groups()
        if (typ == "NOTIFICATION") != use_notifications:
            continue
        f = rest.split(";")
        if typ == "NOTIFICATION":
            f = f[1:]  # drop the contact name
        if kind == "SERVICE":
            if len(f) < 3:
                continue
            host, check, state = f[0], f[1], f[2]
            output = f[-1] if len(f) > 3 else ""
        else:
            if len(f) < 2:
                continue
            host, check, state = f[0], "host down", f[1]
            output = f[-1] if len(f) > 2 else ""
        if typ == "ALERT" and len(f) > 3 and kind == "SERVICE" and f[3] != "HARD":
            continue
        if typ == "ALERT" and kind == "HOST" and len(f) > 2 and f[2] != "HARD":
            continue
        state = state.split(" ")[0].strip("()").upper()  # "DOWNTIMESTART (OK)" and friends
        if state not in NAGIOS_SEV and state not in ("OK", "UP"):
            continue
        out.append(Alert(
            timestamp=datetime.fromtimestamp(int(ts), tz=timezone.utc), source="nagios",
            host=host, check=check, summary=f"{check} on {host}: {output}".strip(": "),
            severity=NAGIOS_SEV.get(state, "info"),
            status="resolve" if state in ("OK", "UP") else "trigger",
        ))
    return out


# Zabbix ------------------------------------------------------------------------------

ZABBIX_SEV = {"disaster": "critical", "high": "critical", "average": "error", "warning": "warning",
              "information": "info", "not classified": "info",
              "5": "critical", "4": "critical", "3": "error", "2": "warning", "1": "info", "0": "info"}


def read_zabbix(text: str) -> list[Alert]:
    out = []
    if text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        data = data.get("result", data) if isinstance(data, dict) else data
        for e in data:
            hosts = e.get("hosts") or [{}]
            host = hosts[0].get("host") or hosts[0].get("name", "")
            name = e.get("name", "")
            out.append(Alert(
                timestamp=_time(e["clock"]), source="zabbix", host=host, check=name,
                summary=f"{name} on {host}", severity=ZABBIX_SEV.get(str(e.get("severity", "")), "warning"),
                status="resolve" if str(e.get("value", "1")) == "0" else "trigger",
                extra={"eventid": str(e.get("eventid", ""))}))
        return out
    for r in _rows(text):
        host, problem = r.get("host", ""), r.get("problem", "")
        sev = ZABBIX_SEV.get(r.get("severity", "").lower(), "warning")
        base = dict(source="zabbix", host=host, check=problem, summary=f"{problem} on {host}",
                    severity=sev, extra={"tags": r.get("tags", "")})
        out.append(Alert(timestamp=_time(r["time"]), status="trigger", **base))
        if r.get("recovery time"):
            out.append(Alert(timestamp=_time(r["recovery time"]), status="resolve", **base))
    return out


# Splunk ------------------------------------------------------------------------------

SPLUNK_ITSI = {"6": "critical", "5": "error", "4": "warning", "3": "warning", "2": "info", "1": "info"}


def read_splunk(text: str) -> list[Alert]:
    out = []
    for r in _rows(text):
        sev = _pick(r, "severity", "urgency", "priority")
        sev = SPLUNK_ITSI.get(sev, normalize_severity(sev) if sev else "warning")
        status = _pick(r, "status", "state").lower()
        check = _pick(r, "search_name", "ss_name", "savedsearch_name", "alert", "title", "rule_name")
        out.append(Alert(
            timestamp=_time(r["_time"]), source="splunk",
            host=_pick(r, "host", "dest", "src_host", "orig_host"),
            check=check, summary=_pick(r, "description", "title", "_raw", default=check)[:300],
            severity=sev, status="resolve" if status in ("closed", "resolved", "5") else "trigger",
            service=_pick(r, "service_name", "app"),
        ))
    return out


# Zenoss ------------------------------------------------------------------------------

ZENOSS_SEV = {"critical": "critical", "error": "error", "warning": "warning", "info": "info",
              "debug": "info", "5": "critical", "4": "error", "3": "warning", "2": "info", "1": "info"}


def read_zenoss(text: str) -> list[Alert]:
    out = []
    for r in _rows(text):
        sev_raw = r.get("severity", "").lower()
        resource, component = r.get("resource", r.get("device", "")), r.get("component", "")
        check = r.get("event class", "") + (f" {component}" if component else "")
        summary = r.get("summary", check)
        first = _time(r.get("first seen") or r["last seen"])
        last = _time(r.get("last seen") or r["first seen"])
        try:
            count = max(1, int(float(r.get("count", "1") or 1)))
        except ValueError:
            count = 1
        if sev_raw in ("clear", "0"):
            out.append(Alert(timestamp=last, source="zenoss", host=resource, check=check.strip(),
                             summary=summary, severity="info", status="resolve"))
            continue
        # Zenoss already folds repeats into one event with a Count. Expand it back into
        # the triggers the team would have received, spread across first to last seen.
        step = (last - first) / (count - 1) if count > 1 else timedelta(0)
        for i in range(count):
            out.append(Alert(timestamp=first + step * i, source="zenoss", host=resource,
                             check=check.strip(), summary=summary,
                             severity=ZENOSS_SEV.get(sev_raw, "warning"), status="trigger"))
        if r.get("status", "").lower() in ("closed", "cleared"):
            out.append(Alert(timestamp=last + timedelta(seconds=1), source="zenoss", host=resource,
                             check=check.strip(), summary=summary, severity="info", status="resolve"))
    return out


# ServiceNow Event Management --------------------------------------------------------------

SNOW_SEV = {"1": "critical", "critical": "critical", "2": "error", "major": "error",
            "3": "warning", "minor": "warning", "4": "warning", "warning": "warning",
            "5": "info", "info": "info", "0": "clear", "clear": "clear"}


def read_servicenow(text: str) -> list[Alert]:
    out = []
    for r in _rows(text):
        sev = SNOW_SEV.get(r.get("severity", "").split(" ")[0].lower(), "warning")
        state = r.get("state", "").lower()
        check = _pick(r, "metric_name", "type", "message_key", "description")
        node = _pick(r, "node", "cmdb_ci")
        out.append(Alert(
            timestamp=_time(_pick(r, "time_of_event", "initial_remote_time", "sys_created_on")),
            source=_pick(r, "source", default="servicenow"), host=node, check=check,
            summary=_pick(r, "description", "short_description", default=check),
            severity="info" if sev == "clear" else sev,
            status="resolve" if sev == "clear" or state in ("closed", "resolved") else "trigger",
            service=r.get("resource", ""), extra={"message_key": r.get("message_key", "")},
        ))
    return out


# Detection ----------------------------------------------------------------------------

def detect(text: str) -> str:
    head = text.lstrip()[:4000]
    if _NAGIOS.search(head) or re.search(r"^\[\d{10}\] (SERVICE|HOST) ", head, re.M):
        return "nagios"
    if head.startswith(("[", "{")):
        low = head.lower()
        if '"conditionname"' in low or '"incidentid"' in low:
            return "newrelic"
        if '"clock"' in low and ('"eventid"' in low or '"value"' in low):
            return "zabbix"
        return "generic"
    h = _header(text)
    if "conditionname" in h or {"incidentid", "event", "priority"} <= h:
        return "newrelic"
    if {"severity", "time", "host", "problem"} <= h:
        return "zabbix"
    if "_time" in h:
        return "splunk"
    if "event class" in h and ("first seen" in h or "last seen" in h):
        return "zenoss"
    if "node" in h and ({"time_of_event", "message_key", "initial_remote_time"} & h):
        return "servicenow"
    return "generic"


READERS = {"newrelic": read_newrelic, "nagios": read_nagios, "zabbix": read_zabbix,
           "splunk": read_splunk, "zenoss": read_zenoss, "servicenow": read_servicenow}


def load(path: str | Path, fmt: str = "auto") -> tuple[list[Alert], str]:
    """Load any supported export. Returns the alerts (time ordered) and the format used."""
    text = Path(path).read_text(encoding="utf-8-sig")
    fmt = detect(text) if fmt == "auto" else fmt
    if fmt == "generic":
        return load_csv(path), fmt
    alerts = READERS[fmt](text)
    if not alerts:
        raise ValueError(f"no alerts found in {path} as a {fmt} export")
    alerts.sort(key=lambda a: a.timestamp)
    return alerts, fmt
