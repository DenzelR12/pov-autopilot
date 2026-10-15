"""Command line entry point: `pdsa <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path

from . import __version__, alerts, design, discover, opsgenie, replay, report, roi, sample, terraform


def _assumptions(args) -> roi.Assumptions:
    kw = {f.name: getattr(args, f.name) for f in fields(roi.Assumptions)
          if getattr(args, f.name, None) is not None}
    return roi.Assumptions(**kw)


def _add_roi_args(p):
    g = p.add_argument_group("business case assumptions")
    for f in fields(roi.Assumptions):
        g.add_argument(f"--{f.name.replace('_', '-')}", dest=f.name,
                       type=int if f.type in ("int", int) else float, default=None)


def cmd_sample(args):
    data = sample.generate(days=args.days, seed=args.seed)
    alerts.write_csv(data, args.out)
    print(f"wrote {len(data):,} alert events to {args.out}")


def cmd_discover(args):
    d = discover.analyze(alerts.load_csv(args.alerts))
    payload = d.to_dict()
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2))
    for stage, count in d.funnel:
        print(f"{count:>8,}  {stage}")
    print(f"\nprojected reduction {d.reduction:.1%}")
    for r in d.recommendations:
        print(f" * {r}")


def cmd_pov(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    d = discover.analyze(alerts.load_csv(args.alerts))
    bc = roi.build(d, _assumptions(args))
    dsg = design.from_discovery(d, args.customer, time_zone=args.tz)
    (out / "discovery.json").write_text(json.dumps(d.to_dict(), indent=2))
    (out / "business_case.json").write_text(json.dumps(bc.to_dict(), indent=2))
    (out / "design.json").write_text(json.dumps(dsg.to_dict(), indent=2))
    (out / "summary.md").write_text(report.markdown(d, bc, args.customer))
    (out / "report.html").write_text(report.html_report(d, bc, args.customer))
    terraform.write(dsg, out / "terraform")
    print(f"{args.customer}: {d.raw:,} triggers to {d.pages:,} pages "
          f"({d.reduction:.0%} reduction), modeled value ${bc.total_value_year:,.0f}/yr")
    print(f"wrote report.html, summary.md, discovery.json, business_case.json, design.json, "
          f"terraform/ to {out}")


def cmd_migrate(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bundle = opsgenie.pull(region=args.region) if args.pull else opsgenie.load_bundle(args.bundle)
    if args.pull:
        (out / "opsgenie_export.json").write_text(json.dumps(bundle, indent=2))
    dsg = opsgenie.convert(bundle, name=args.customer)
    terraform.write(dsg, out / "terraform")
    (out / "design.json").write_text(json.dumps(dsg.to_dict(), indent=2))
    gaps = "\n".join(f"{i}. {n}" for i, n in enumerate(dsg.notes, 1))
    (out / "migration_gaps.md").write_text(f"# {args.customer}: migration gaps\n\n{gaps}\n")
    print(f"converted {len(dsg.users)} users, {len(dsg.schedules)} schedules, "
          f"{len(dsg.escalation_policies)} escalation policies, {len(dsg.services)} services")
    print(f"{len(dsg.notes)} items for review in {out / 'migration_gaps.md'}")


def cmd_replay(args):
    data = alerts.load_csv(args.alerts)
    if args.storm is not None:
        d = discover.analyze(list(data))
        storms = sorted(d.storms, key=lambda s: -s["alerts"])
        if not storms:
            sys.exit("no storms found in this file")
        from datetime import timedelta
        start = alerts.parse_time(storms[args.storm]["start"]) - timedelta(minutes=2)
        data = replay.select_window(data, start, args.minutes)
    if args.limit:
        data = data[: args.limit]
    key = args.routing_key or os.environ.get("PD_ROUTING_KEY", "")
    if args.send and not key:
        sys.exit("--send needs --routing-key or PD_ROUTING_KEY")
    n = replay.replay(data, key or "dry-run", speed=args.speed, live=args.send)
    print(f"{'sent' if args.send else 'dry run:'} {n} events", file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pdsa", description="Solutions Architect autopilot for PagerDuty POVs")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="generate a realistic synthetic alert history")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="alerts.csv")
    s.set_defaults(fn=cmd_sample)

    s = sub.add_parser("discover", help="noise funnel and recommendations from an alert export")
    s.add_argument("alerts")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("pov", help="full pack: report, business case, success plan, Terraform")
    s.add_argument("alerts")
    s.add_argument("--customer", default="Customer")
    s.add_argument("--tz", default="America/New_York")
    s.add_argument("--out", default="pov_pack")
    _add_roi_args(s)
    s.set_defaults(fn=cmd_pov)

    s = sub.add_parser("migrate", help="convert an Opsgenie config to PagerDuty Terraform")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle", help="exported JSON bundle")
    src.add_argument("--pull", action="store_true", help="pull live via OPSGENIE_API_KEY")
    s.add_argument("--region", choices=["us", "eu"], default="us")
    s.add_argument("--customer", default="opsgenie migration")
    s.add_argument("--out", default="migration")
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("replay", help="replay alert history into PagerDuty (dry run by default)")
    s.add_argument("alerts")
    s.add_argument("--routing-key")
    s.add_argument("--storm", type=int, help="replay the Nth largest storm (0 = worst)")
    s.add_argument("--minutes", type=int, default=20)
    s.add_argument("--speed", type=float, default=60.0, help="time compression factor")
    s.add_argument("--limit", type=int)
    s.add_argument("--send", action="store_true", help="actually send to the Events API")
    s.set_defaults(fn=cmd_replay)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
