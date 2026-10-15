"""Customer facing outputs: an executive HTML report, a markdown summary,
and a POV success plan generated from the discovery data."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from .discover import Discovery
from .roi import BusinessCase


def success_plan(d: Discovery) -> list[dict]:
    weeks = max(d.window["days"] / 7, 1)
    pages_wk = d.pages / weeks
    raw_wk = d.raw / weeks
    top = sorted(d.services.items(), key=lambda kv: -kv[1]["raw_triggers"])[:3]
    plan = [
        {"criterion": "All monitoring sources send to one global routing key",
         "measure": f"{len(d.by_source)} sources integrated ({', '.join(d.by_source)})",
         "target": "Week 1"},
        {"criterion": "Noise reduction in production traffic",
         "measure": f"High urgency pages per week vs. {raw_wk:,.0f} raw alerts per week today",
         "target": f"At most {pages_wk * 1.1:,.0f} pages per week ({d.reduction:.0%} reduction or better)"},
    ]
    for name, s in top:
        plan.append({
            "criterion": f"'{name}' team gets signal, not noise",
            "measure": f"{s['raw_triggers']:,} raw alerts in the sample window",
            "target": f"At most {max(s['projected_pages'], 1):,} pages over the same window",
        })
    if d.storms:
        worst = max(d.storms, key=lambda st: st["alerts"])
        plan.append({
            "criterion": "Replay the worst historical storm",
            "measure": f"{worst['alerts']} alerts across {len(worst['services'])} services "
                       f"on {worst['start'][:10]}",
            "target": f"Grouped into {len(worst['services'])} incidents or fewer, "
                      f"root '{worst['likely_root']}' paged first",
        })
    if d.off_hours_share > 0:
        plan.append({
            "criterion": "Protect sleep",
            "measure": f"{d.off_hours_share:.0%} of projected pages are off hours",
            "target": "Low urgency alerts never page off hours",
        })
    return plan


def _money(v: float) -> str:
    return f"${v:,.0f}"


def markdown(d: Discovery, bc: BusinessCase | None, customer: str) -> str:
    lines = [f"# {customer}: incident noise discovery", "",
             f"Window: {d.window['start'][:10]} to {d.window['end'][:10]} ({d.window['days']} days)", "",
             "## Alert to page funnel", "", "| Stage | Count |", "|---|---:|"]
    lines += [f"| {s} | {c:,} |" for s, c in d.funnel]
    lines += ["", f"**Projected reduction: {d.reduction:.1%}** "
              f"({d.raw:,} triggers to {d.pages:,} pages)", "", "## Recommendations", ""]
    lines += [f"{i}. {r}" for i, r in enumerate(d.recommendations, 1)]
    if bc:
        lines += ["", "## Business case (annual, editable assumptions)", "",
                  f"* Toil reclaimed: {bc.hours_reclaimed_month:,} engineer hours per month "
                  f"({_money(bc.toil_savings_year)} per year)",
                  f"* Downtime avoided: {bc.downtime_min_avoided_year:,} minutes "
                  f"({_money(bc.downtime_savings_year)} per year)",
                  f"* Faster response: {_money(bc.responder_savings_year)} per year",
                  f"* **Total modeled value: {_money(bc.total_value_year)} per year**"]
        if bc.payback_months:
            lines.append(f"* Payback: {bc.payback_months} months")
    lines += ["", "## POV success plan", "", "| Criterion | Measure | Target |", "|---|---|---|"]
    lines += [f"| {p['criterion']} | {p['measure']} | {p['target']} |" for p in success_plan(d)]
    return "\n".join(lines) + "\n"


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f21;--muted:#62666b;--line:#e3e3df;--accent:#0a7d4f;--warn:#b54708;--bar:#0a7d4f}
@media (prefers-color-scheme:dark){:root{--bg:#121413;--card:#1b1e1c;--ink:#ecedeb;--muted:#a2a7a3;--line:#2c312e;--accent:#4cc38a;--warn:#f0a35e;--bar:#4cc38a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}.kpi b{display:block;font-size:28px}.kpi span{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow-x:auto}
.row{display:grid;grid-template-columns:minmax(140px,280px) 1fr 80px;gap:12px;align-items:center;margin:8px 0;font-size:14px}
.track{background:var(--line);border-radius:4px;height:14px}.fill{background:var(--bar);height:14px;border-radius:4px}
.num{text-align:right;font-variant-numeric:tabular-nums}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}ol li{margin:6px 0}.note{color:var(--muted);font-size:13px}
@media (max-width:600px){.row{grid-template-columns:1fr 70px}.row .track{grid-column:1/3;grid-row:2}}
"""


def html_report(d: Discovery, bc: BusinessCase | None, customer: str,
                migration_notes: list[str] | None = None) -> str:
    e = html.escape
    raw = max(d.raw, 1)
    funnel = "".join(
        f'<div class="row"><div>{e(s)}</div><div class="track"><div class="fill" '
        f'style="width:{max(c / raw * 100, 0.6):.1f}%"></div></div><div class="num">{c:,}</div></div>'
        for s, c in d.funnel
    )
    kpis = [
        (f"{d.raw:,}", "raw alert triggers"),
        (f"{d.pages:,}", "projected high urgency pages"),
        (f"{d.reduction:.0%}", "projected noise reduction"),
        (f"{len(d.storms)}", "cross service storms"),
    ]
    if bc:
        kpis.append((_money(bc.total_value_year), "modeled annual value"))
    kpi_html = "".join(f'<div class="kpi"><b>{e(v)}</b><span>{e(k)}</span></div>' for v, k in kpis)

    recs = "".join(f"<li>{e(r)}</li>" for r in d.recommendations)
    svc_rows = "".join(
        f"<tr><td>{e(n)}</td><td>{e(s['team'])}</td><td>{e(', '.join(s['sources']))}</td>"
        f"<td class='n'>{s['raw_triggers']:,}</td><td class='n'>{s['projected_incidents']:,}</td>"
        f"<td class='n'>{s['projected_pages']:,}</td></tr>"
        for n, s in sorted(d.services.items(), key=lambda kv: -kv[1]["raw_triggers"])
    )
    storm_rows = "".join(
        f"<tr><td>{e(st['start'][:16].replace('T', ' '))}</td><td class='n'>{st['alerts']}</td>"
        f"<td>{e(', '.join(st['services']))}</td><td>{e(st['likely_root'])}</td></tr>"
        for st in sorted(d.storms, key=lambda st: -st["alerts"])[:10]
    ) or "<tr><td colspan=4>None detected</td></tr>"
    flap_rows = "".join(
        f"<tr><td>{e(f['check'])}</td><td>{e(f['host'])}</td><td>{e(f['service'])}</td>"
        f"<td class='n'>{f['cycles']}</td><td class='n'>{f['median_open_min']}</td></tr>"
        for f in d.flapping[:10]
    ) or "<tr><td colspan=5>None detected</td></tr>"
    plan_rows = "".join(
        f"<tr><td>{e(p['criterion'])}</td><td>{e(p['measure'])}</td><td>{e(p['target'])}</td></tr>"
        for p in success_plan(d)
    )

    bc_html = ""
    if bc:
        a = bc.assumptions
        bc_html = f"""<h2>Business case</h2><div class="card"><table>
<tr><th>Value driver</th><th>Result</th><th>Driven by</th></tr>
<tr><td>Toil reclaimed</td><td>{bc.hours_reclaimed_month:,} hours per month, {_money(bc.toil_savings_year)} per year</td>
<td>{bc.interruptions_avoided_month:,} interruptions avoided per month, {a.minutes_lost_per_interruption:g} min each,
{_money(a.engineer_cost_per_hour)}/hr, {a.off_hours_multiplier:g}x off hours</td></tr>
<tr><td>Downtime avoided</td><td>{bc.downtime_min_avoided_year:,} min, {_money(bc.downtime_savings_year)} per year</td>
<td>{a.mttr_improvement:.0%} faster than {a.current_mttr_min:g} min MTTR on {a.customer_impacting_share:.0%} of pages,
{_money(a.downtime_cost_per_min)}/min</td></tr>
<tr><td>Responder time</td><td>{_money(bc.responder_savings_year)} per year</td>
<td>{a.responders_per_incident:g} responders per incident</td></tr>
<tr><th>Total modeled value</th><th>{_money(bc.total_value_year)} per year</th><th></th></tr>
</table><p class="note">A model, not a promise. Every input is a placeholder to replace with the customer's own numbers.</p></div>"""

    mig_html = ""
    if migration_notes:
        mig_html = "<h2>Migration gaps to review</h2><div class='card'><ol>" + "".join(
            f"<li>{e(n)}</li>" for n in migration_notes) + "</ol></div>"

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(customer)} Noise Discovery</title>
<style>{CSS}</style></head><body><main>
<h1>{e(customer)}: incident noise discovery</h1>
<p class="sub">{e(d.window['start'][:10])} to {e(d.window['end'][:10])} · {d.window['days']} days of alert history · generated {generated}</p>
<div class="kpis">{kpi_html}</div>
<h2>From alerts to pages</h2><div class="card">{funnel}
<p class="note">Estimates from explainable rules (dedup by fingerprint, flap suppression, {e(d.funnel[3][0].split('(')[1].rstrip(')'))} grouping, severity based urgency). Platform grouping in the POV should meet or beat them.</p></div>
<h2>Recommendations</h2><div class="card"><ol>{recs}</ol></div>
<h2>POV success plan</h2><div class="card"><table><tr><th>Criterion</th><th>Measure</th><th>Target</th></tr>{plan_rows}</table></div>
{bc_html}
<h2>Services discovered</h2><div class="card"><table><tr><th>Service</th><th>Team</th><th>Sources</th><th class="num">Raw</th><th class="num">Incidents</th><th class="num">Pages</th></tr>{svc_rows}</table></div>
<h2>Cross service storms</h2><div class="card"><table><tr><th>Start (UTC)</th><th class="num">Alerts</th><th>Services</th><th>Likely root</th></tr>{storm_rows}</table></div>
<h2>Flapping checks</h2><div class="card"><table><tr><th>Check</th><th>Host</th><th>Service</th><th class="num">Cycles</th><th class="num">Median open (min)</th></tr>{flap_rows}</table></div>
{mig_html}
</main></body></html>
"""
