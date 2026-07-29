"""CLI entry point. Registers stages and drives them over a Run.

Adding a stage is one line in STAGES. Each stage is a callable taking the
current runs.Run; order in the dict is pipeline order.
"""

import argparse
import logging
import sys

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
    args = parser.parse_args(argv)

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
