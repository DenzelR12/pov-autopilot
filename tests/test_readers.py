"""Each monitoring tool export a prospect might send is detected and normalized."""

import json

import pytest

from pdsa import discover, readers

NEWRELIC_CSV = """timestamp,incidentId,event,priority,conditionName,policyName,entity.name,title
1790846254000,101,open,critical,High error rate,Checkout,checkout-web-1,High error rate on checkout-web-1
1790846314000,101,close,critical,High error rate,Checkout,checkout-web-1,High error rate on checkout-web-1
1790846400000,102,open,medium,Apdex low,Checkout,checkout-web-2,Apdex below 0.8
"""

NEWRELIC_JSON = json.dumps({"results": [
    {"timestamp": 1790846254000, "incidentId": 7, "event": "open", "priority": "high",
     "conditionName": "Disk full", "policyName": "Infra", "entity.name": "db-01", "title": "Disk full"}]})

NAGIOS_LOG = """[1790846254] SERVICE ALERT: web-01;HTTP;CRITICAL;SOFT;1;HTTP CRITICAL - Socket timeout
[1790846314] SERVICE ALERT: web-01;HTTP;CRITICAL;HARD;3;HTTP CRITICAL - Socket timeout
[1790846320] SERVICE NOTIFICATION: oncall;web-01;HTTP;CRITICAL;notify-service-by-email;HTTP CRITICAL - Socket timeout
[1790846500] HOST NOTIFICATION: oncall;db-01;DOWN;notify-host-by-sms;PING CRITICAL - Packet loss = 100%
[1790846900] SERVICE NOTIFICATION: oncall;web-01;HTTP;OK;notify-service-by-email;HTTP OK
[1790846950] Auto-save of retention data completed successfully.
"""

NAGIOS_ALERTS_ONLY = """[1790846254] SERVICE ALERT: web-01;Disk;WARNING;SOFT;1;DISK WARNING
[1790846314] SERVICE ALERT: web-01;Disk;WARNING;HARD;3;DISK WARNING - 12% free
[1790846600] SERVICE ALERT: web-01;Disk;OK;HARD;3;DISK OK
"""

ZABBIX_CSV = """Severity,Time,Recovery time,Status,Info,Host,Problem,Duration,Ack,Actions,Tags
High,2026-10-01 05:37:34,2026-10-01 05:52:00,RESOLVED,,app-01,High CPU utilization,14m 26s,No,,
Disaster,2026-10-01 05:38:00,,PROBLEM,,db-01,MySQL is down,2h,No,,service:db
Information,2026-10-01 06:00:00,2026-10-01 06:01:00,RESOLVED,,app-02,Agent restarted,1m,No,,
"""

ZABBIX_JSON = json.dumps({"result": [
    {"eventid": "11", "clock": "1790846254", "value": "1", "severity": "4", "name": "High CPU",
     "hosts": [{"host": "app-01"}]},
    {"eventid": "12", "clock": "1790846854", "value": "0", "severity": "4", "name": "High CPU",
     "hosts": [{"host": "app-01"}]}]})

SPLUNK_CSV = """_time,host,search_name,severity,status,description
2026-10-01T05:37:34.000+00:00,web-01,Checkout 5xx spike,high,new,5xx above threshold
2026-10-01T05:38:34.000+00:00,web-01,Checkout 5xx spike,high,new,5xx above threshold
2026-10-01T05:50:00.000+00:00,web-02,Login failures,6,closed,Login failure burst
"""

ZENOSS_CSV = """Status,Severity,Resource,Component,Event Class,Summary,First Seen,Last Seen,Count
New,Critical,sw-core-01,eth1,/Net/Link,Interface down,2026/10/01 05:00:00,2026/10/01 05:30:00,4
Closed,Warning,app-01,,/Perf/CPU,CPU above 90,2026/10/01 06:00:00,2026/10/01 06:00:00,1
Cleared,Clear,app-02,,/Status/Ping,Ping restored,2026/10/01 07:00:00,2026/10/01 07:00:00,1
"""

SNOW_CSV = """number,time_of_event,source,node,type,resource,metric_name,severity,description,message_key,state
EVT1,2026-10-01 05:37:34,SolarWinds,rtr-01,Interface,Gi0/1,link_status,1,Link down,rtr-01:Gi0/1,Ready
EVT2,2026-10-01 05:40:00,SolarWinds,rtr-01,Interface,Gi0/1,link_status,0,Link restored,rtr-01:Gi0/1,Processed
EVT3,2026-10-01 05:41:00,SCOM,app-03,Service,IIS,service_state,3 - Minor,IIS slow,app-03:IIS,Ready
"""

GENERIC_CSV = """timestamp,source,host,check,summary,severity,status
2026-10-01T05:37:34Z,datadog,chk-01,http 5xx rate high,5xx high,error,trigger
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


@pytest.mark.parametrize("name,text,fmt", [
    ("nr.csv", NEWRELIC_CSV, "newrelic"), ("nr.json", NEWRELIC_JSON, "newrelic"),
    ("nagios.log", NAGIOS_LOG, "nagios"), ("problems.csv", ZABBIX_CSV, "zabbix"),
    ("events.json", ZABBIX_JSON, "zabbix"), ("splunk.csv", SPLUNK_CSV, "splunk"),
    ("zenoss.csv", ZENOSS_CSV, "zenoss"), ("em_event.csv", SNOW_CSV, "servicenow"),
    ("alerts.csv", GENERIC_CSV, "generic"),
])
def test_format_is_detected_and_loads(tmp_path, name, text, fmt):
    alerts, used = readers.load(write(tmp_path, name, text))
    assert used == fmt and alerts
    assert all(a.timestamp.tzinfo for a in alerts)
    assert alerts == sorted(alerts, key=lambda a: a.timestamp)
    discover.analyze(alerts)  # the whole pipeline accepts it


def test_newrelic_maps_priority_events_and_entities(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "nr.csv", NEWRELIC_CSV))
    assert [(a.status, a.severity, a.host) for a in alerts] == [
        ("trigger", "critical", "checkout-web-1"), ("resolve", "critical", "checkout-web-1"),
        ("trigger", "warning", "checkout-web-2")]
    assert alerts[0].check == "High error rate" and alerts[0].service == "Checkout"


def test_nagios_prefers_notifications_and_skips_soft_states(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "nagios.log", NAGIOS_LOG))
    assert [(a.host, a.check, a.status, a.severity) for a in alerts] == [
        ("web-01", "HTTP", "trigger", "critical"), ("db-01", "host down", "trigger", "critical"),
        ("web-01", "HTTP", "resolve", "info")]
    alerts, _ = readers.load(write(tmp_path, "alerts.log", NAGIOS_ALERTS_ONLY))
    assert [(a.status, a.severity) for a in alerts] == [("trigger", "warning"), ("resolve", "info")]


def test_zabbix_problem_rows_become_trigger_and_recovery(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "problems.csv", ZABBIX_CSV))
    assert len(alerts) == 5  # 3 triggers, 2 recoveries (MySQL is still open)
    db = [a for a in alerts if a.host == "db-01"]
    assert len(db) == 1 and db[0].severity == "critical" and db[0].status == "trigger"
    assert {a.severity for a in alerts if a.host == "app-02"} == {"info"}
    alerts, _ = readers.load(write(tmp_path, "events.json", ZABBIX_JSON))
    assert [a.status for a in alerts] == ["trigger", "resolve"] and alerts[0].severity == "critical"


def test_splunk_names_severity_and_itsi_scale(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "splunk.csv", SPLUNK_CSV))
    assert alerts[0].check == "Checkout 5xx spike" and alerts[0].severity == "error"
    assert alerts[2].severity == "critical" and alerts[2].status == "resolve"


def test_zenoss_count_expands_into_triggers(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "zenoss.csv", ZENOSS_CSV))
    link = [a for a in alerts if a.host == "sw-core-01"]
    assert len(link) == 4 and all(a.status == "trigger" and a.severity == "critical" for a in link)
    assert (link[-1].timestamp - link[0].timestamp).total_seconds() == 1800
    cpu = [a for a in alerts if a.host == "app-01"]
    assert [a.status for a in cpu] == ["trigger", "resolve"]
    assert [a.status for a in alerts if a.host == "app-02"] == ["resolve"]


def test_servicenow_clear_severity_resolves(tmp_path):
    alerts, _ = readers.load(write(tmp_path, "em_event.csv", SNOW_CSV))
    assert [(a.host, a.severity, a.status) for a in alerts] == [
        ("rtr-01", "critical", "trigger"), ("rtr-01", "info", "resolve"), ("app-03", "warning", "trigger")]
    assert alerts[0].source == "SolarWinds" and alerts[0].fingerprint == alerts[1].fingerprint


def test_us_style_dates_with_am_pm():
    assert readers._time("10/1/2026 5:37 PM").hour == 17
    assert readers._time("2026/10/01 05:37:34").minute == 37


def test_explicit_format_and_empty_export_error(tmp_path):
    p = write(tmp_path, "x.csv", "Severity,Time,Host,Problem\n")
    with pytest.raises(ValueError, match="no alerts"):
        readers.load(p, fmt="zabbix")
