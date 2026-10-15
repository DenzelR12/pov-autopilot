import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdsa import alerts, design, discover, opsgenie, replay, report, roi, sample, terraform
from pdsa.alerts import Alert
from pdsa.cli import main

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def mk(minutes, check="cpu high", host="web-01", sev="error", status="trigger", summary=None):
    return Alert(T0 + timedelta(minutes=minutes), "datadog", host, check,
                 summary or f"[web] {check} on {host} at {minutes}%", sev, status)


@pytest.fixture(scope="module")
def sample_alerts():
    return sample.generate(days=14, seed=3)


# Normalization ------------------------------------------------------------

def test_fingerprint_ignores_live_values_but_not_host():
    a, b = mk(0, summary="cpu at 91%"), mk(1, summary="cpu at 97%")
    assert a.fingerprint == b.fingerprint
    assert mk(0).fingerprint != mk(0, host="web-02").fingerprint


def test_load_csv_maps_common_column_aliases(tmp_path):
    p = tmp_path / "dd.csv"
    p.write_text("created_at,monitor_name,hostname,priority,state,message,team\n"
                 "2026-09-01T00:00:00Z,disk full,db-1,P1,triggered,[db] disk full,data\n"
                 "1788220860,disk full,db-1,P1,OK,[db] disk full,data\n")
    rows = alerts.load_csv(p)
    assert [r.severity for r in rows] == ["critical", "critical"]
    assert [r.status for r in rows] == ["trigger", "resolve"]
    assert rows[0].extra["team"] == "data"


def test_load_csv_rejects_missing_timestamp(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("check,host\nx,y\n")
    with pytest.raises(ValueError):
        alerts.load_csv(p)


# Discovery ----------------------------------------------------------------

def test_dedup_collapses_refires_until_resolve():
    rows = [mk(0), mk(5), mk(10), mk(12, status="resolve"), mk(30)]
    d = discover.analyze(rows)
    assert d.funnel[0][1] == 4  # raw triggers
    assert d.funnel[1][1] == 2  # two distinct open periods


def test_alert_without_resolve_times_out():
    rows = [mk(0), mk(60 * 5)]
    d = discover.analyze(rows, discover.Settings(auto_resolve_hours=4))
    assert d.funnel[1][1] == 2


def test_flapping_is_detected_and_suppressed():
    rows = []
    for i in range(6):
        rows += [mk(i * 60), mk(i * 60 + 3, status="resolve")]
    d = discover.analyze(rows)
    assert len(d.flapping) == 1 and d.flapping[0]["cycles"] == 6
    assert d.funnel[2][1] == 0


def test_grouping_window_is_fixed_not_chained():
    rows = [mk(0, host=f"h{i}") for i in range(1)] + [mk(4, host="h2"), mk(8, host="h3"), mk(12, host="h4")]
    d = discover.analyze(rows, discover.Settings(group_window_min=5))
    assert d.funnel[3][1] == 2  # [0,4] and [8,12], not one chained incident


def test_severity_controls_paging():
    d = discover.analyze([mk(0, sev="warning"), mk(30, host="x", sev="critical")])
    assert d.pages == 1


def test_sample_produces_storms_and_meaningful_reduction(sample_alerts):
    d = discover.analyze(sample_alerts)
    assert d.storms, "cascading outages should register as storms"
    assert 0.5 < d.reduction < 0.99
    assert all(s["team"] != "unassigned" for s in d.services.values())
    assert d.recommendations


# Business case ------------------------------------------------------------

def test_roi_scales_with_assumptions(sample_alerts):
    d = discover.analyze(sample_alerts)
    low = roi.build(d, roi.Assumptions(engineer_cost_per_hour=50))
    high = roi.build(d, roi.Assumptions(engineer_cost_per_hour=200))
    assert high.toil_savings_year == pytest.approx(low.toil_savings_year * 4, rel=0.01)
    assert low.payback_months is None
    priced = roi.build(d, roi.Assumptions(seats=10, price_per_seat_month=50))
    assert priced.payback_months and priced.payback_months > 0


# Design and Terraform -----------------------------------------------------

def _parse_all(files):
    hcl2 = pytest.importorskip("hcl2")
    for name, text in files.items():
        if name.endswith(".tf"):
            hcl2.loads(text)


def test_discovery_terraform_is_valid_hcl_and_complete(sample_alerts):
    d = discover.analyze(sample_alerts)
    dsg = design.from_discovery(d, "Acme")
    files = terraform.render(dsg)
    _parse_all(files)
    main_tf = files["main.tf"]
    for svc in dsg.services:
        assert f'resource "pagerduty_service" "{design.slug(svc.name)}"' in main_tf
    assert "pagerduty_event_orchestration_router" in main_tf
    assert "suspend = 300" in main_tf
    assert 'urgency = "severity_based"' in main_tf
    assert "team_members" in files["variables.tf"]


def test_hcl_strings_are_escaped():
    assert terraform.q('a "b" ${c}') == '"a \\"b\\" $${c}"'


# Opsgenie -----------------------------------------------------------------

@pytest.fixture(scope="module")
def og_design():
    return opsgenie.convert(opsgenie.load_bundle(ROOT / "examples" / "opsgenie_export.json"), "Acme")


def test_opsgenie_delays_become_gaps(og_design):
    ep = next(p for p in og_design.escalation_policies if p.name == "commerce escalation")
    # Opsgenie absolute delays 0, 10, 25 -> PagerDuty waits 10, 15, then default
    assert [r.delay_min for r in ep.rules] == [10, 15, opsgenie.DEFAULT_LAST_DELAY_MIN]
    assert ep.num_loops == 2


def test_opsgenie_restrictions_convert(og_design):
    plat = next(s for s in og_design.schedules if s.name == "platform business hours")
    r = plat.layers[0].restrictions[0]
    assert r == {"type": "daily_restriction", "start_time_of_day": "08:00:00", "duration_seconds": 37800}
    weekend = next(s for s in og_design.schedules if s.name == "commerce primary").layers[1]
    w = weekend.restrictions[0]
    assert w["type"] == "weekly_restriction" and w["start_day_of_week"] == 6
    assert w["duration_seconds"] == 48 * 3600


def test_opsgenie_gaps_are_reported(og_design):
    notes = "\n".join(og_design.notes)
    for expected in ("'no one' slot", "notify 'next'", "if-not-closed", "legacy-batch", "waited 5 min"):
        assert expected in notes
    assert any(u["email"] == "frank.lee@acme.example" for u in og_design.users)


def test_opsgenie_terraform_is_valid_hcl(og_design):
    files = terraform.render(og_design)
    _parse_all(files)
    assert 'resource "pagerduty_user"' in files["main.tf"]
    assert "default_escalation_policy_id" in files["variables.tf"]


# Replay -------------------------------------------------------------------

def test_replay_events_match_events_api_v2():
    a, r = mk(0), mk(5, status="resolve")
    ev = replay.to_event(a, "key")
    assert ev["event_action"] == "trigger" and ev["payload"]["severity"] == "error"
    assert set(ev["payload"]) >= {"summary", "source", "severity"}
    assert replay.to_event(r, "key")["dedup_key"] == ev["dedup_key"]


def test_replay_dry_run_never_leaks_key():
    out = []
    replay.replay([mk(0)], "secret-key", out=out.append)
    assert "secret-key" not in out[0]


def test_replay_time_compression():
    waits = [w for w, _ in replay.schedule([mk(0), mk(10)], speed=60, min_gap=0)]
    assert waits == [0.0, 10.0]


# Reports and CLI ----------------------------------------------------------

def test_success_plan_and_reports(sample_alerts):
    d = discover.analyze(sample_alerts)
    bc = roi.build(d)
    plan = report.success_plan(d)
    assert len(plan) >= 4
    html = report.html_report(d, bc, "Acme <Retail>")
    assert "Acme &lt;Retail&gt;" in html and "<script" not in html
    assert "POV success plan" in report.markdown(d, bc, "Acme")


def test_cli_end_to_end(tmp_path, capsys):
    csv_path = tmp_path / "alerts.csv"
    main(["sample", "--days", "7", "--out", str(csv_path)])
    main(["pov", str(csv_path), "--customer", "Acme", "--out", str(tmp_path / "pack")])
    for f in ("report.html", "summary.md", "discovery.json", "business_case.json", "terraform/main.tf"):
        assert (tmp_path / "pack" / f).exists(), f
    assert json.loads((tmp_path / "pack" / "discovery.json").read_text())["funnel"]
    main(["migrate", "--bundle", str(ROOT / "examples" / "opsgenie_export.json"),
          "--out", str(tmp_path / "mig")])
    assert (tmp_path / "mig" / "migration_gaps.md").exists()
    main(["replay", str(csv_path), "--storm", "0", "--limit", "5"])
    assert capsys.readouterr().out.count('"event_action"') == 5
