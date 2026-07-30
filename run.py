"""CLI entry point. Registers stages and drives them over a Run.

Adding a stage is one line in STAGES. Each stage is a callable taking the
current runs.Run; order in the dict is pipeline order.
"""

import argparse
import logging
import sys

import config
import runs
import stages.draft
import stages.find
import stages.pick
import stages.report

log = logging.getLogger("run")

# name -> callable(run). One line per stage; dict order is pipeline order.
STAGES: dict = {
    "find": stages.find.run,
    "pick": stages.pick.run,
    "report": stages.report.run,
    "draft": stages.draft.run,
}


def _print_costs() -> int:
    """Spend per purpose per run, so the split across models is visible
    without arithmetic."""
    try:
        entries = runs.read_jsonl(config.COSTS_FILE)
    except runs.MissingDataFileError:
        print("no costs recorded yet")
        return 0

    by_run: dict[str, dict[str, dict]] = {}
    for entry in entries:
        purpose = entry.get("purpose", "?")
        bucket = by_run.setdefault(entry["run_id"], {}).setdefault(
            purpose, {"calls": 0, "cost": 0.0, "input": 0, "cached": 0,
                      "output": 0, "model": entry.get("model", "?")})
        bucket["calls"] += 1
        bucket["cost"] += entry.get("cost_usd", 0.0)
        bucket["input"] += entry["tokens"]["input"]
        bucket["cached"] += entry["tokens"].get("cached_input", 0)
        bucket["output"] += entry["tokens"]["output"]

    total = 0.0
    for run_id in sorted(by_run):
        run_total = sum(b["cost"] for b in by_run[run_id].values())
        total += run_total
        print(f"\n{run_id}   ${run_total:.5f}")
        for purpose, b in sorted(by_run[run_id].items()):
            cache_pct = (100 * b["cached"] / b["input"]) if b["input"] else 0
            print(f"  {purpose:16} {b['calls']:>3} call(s)  ${b['cost']:.5f}  "
                  f"in={b['input']:>7} ({cache_pct:.0f}% cached) out={b['output']:>6}  "
                  f"{b['model'].split('/')[-1]}")

    print(f"\ntotal across {len(by_run)} run(s): ${total:.5f}")
    by_purpose: dict[str, float] = {}
    for run_data in by_run.values():
        for purpose, b in run_data.items():
            by_purpose[purpose] = by_purpose.get(purpose, 0.0) + b["cost"]
    for purpose, cost in sorted(by_purpose.items(), key=lambda kv: -kv[1]):
        share = 100 * cost / total if total else 0
        print(f"  {purpose:16} ${cost:.5f}  ({share:.0f}%)")
    return 0


def _resolve_run(args) -> runs.Run:
    if args.run_id:
        return runs.Run.resume(args.run_id)
    if args.resume:
        return runs.Run.latest()
    return runs.Run.new()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="HyperFeed: one fresh AI news story per day into a short video.")
    parser.add_argument("--stage", metavar="NAME",
                        help=f"run a single stage (registered: {', '.join(STAGES) or 'none yet'})")
    parser.add_argument("--run-id", metavar="ID",
                        help="operate on an existing run directory")
    parser.add_argument("--resume", action="store_true",
                        help="continue the most recent run")
    parser.add_argument("--verbose", action="store_true",
                        help="debug logging")
    parser.add_argument("--costs", action="store_true",
                        help="print spend per purpose per run and exit")
    args = parser.parse_args(argv)

    if args.costs:
        return _print_costs()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.stage:
        if args.stage not in STAGES:
            parser.error(f"unknown stage {args.stage!r} "
                         f"(registered: {', '.join(STAGES) or 'none yet'})")
        selected = [args.stage]
    else:
        selected = list(STAGES)

    if not selected:
        log.error("no stages registered yet — this is the pipeline skeleton")
        return 1

    run = _resolve_run(args)
    log.info("run %s", run.run_id)
    for name in selected:
        log.info("stage %s", name)
        STAGES[name](run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
