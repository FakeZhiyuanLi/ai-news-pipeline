"""CLI entry point. Registers stages and drives them over a Run.

Adding a stage is one line in STAGES. Each stage is a callable taking the
current runs.Run; order in the dict is pipeline order.
"""

import argparse
import logging
import sys

import config
import runs
import stages.check
import stages.draft
import stages.find
import stages.narrate
import stages.pick
import stages.report

log = logging.getLogger("run")

# name -> callable(run). One line per stage; dict order is pipeline order.
STAGES: dict = {
    "find": stages.find.run,
    "pick": stages.pick.run,
    "report": stages.report.run,
    "draft": stages.draft.run,
    "check": stages.check.run,
    "narrate": stages.narrate.run,
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


def _selftest() -> int:
    """Run draft, check and both pure checkers against committed fixtures with
    every model call replayed from recorded responses.

    Zero API calls, zero rate limit, seconds to run. Most of the previous
    build's bugs were silent, and this is what catches that class.
    """
    import json

    import llm
    from checks import claims as claim_checks
    from checks import numbers, quotes, sentences
    import stages.draft
    import stages.check

    failures = []

    def check(label, condition, detail=""):
        print(f"{'ok  ' if condition else 'FAIL'} {label}"
              + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    print("--- pure checkers (no model, no network) ---")
    source = 'She said, "the model ships in March," at the briefing.'
    check("verbatim quote passes", quotes.verify("the model ships in March", source))
    check("one changed word drops",
          not quotes.verify("the model ships in April", source))
    gpu = config.REFERENCE_SCALES["midrange_gpu_price_usd"]["value"]
    check("correct scale anchor passes", numbers.verify(f"15000 / {gpu}", 27.3))
    check("anchor off by 3x drops", not numbers.verify(f"15000 / {gpu}", 81.8))
    check("unknown scale rejected",
          not numbers.verify("15000 / average_uk_salary_gbp", 27.3))
    check("splitter: B200. torch.compile",
          sentences.split("B200. torch.compile lifts speed.")
          == ["B200.", "torch.compile lifts speed."])
    check("splitter: U.S. is not a boundary",
          sentences.split("Only logged-in U.S. residents qualify.")
          == ["Only logged-in U.S. residents qualify."])

    print("\n--- claim routing (no model) ---")
    slots = {"quote": {"text": "the model ships in March", "name": "A", "role": "B"},
             "scale_anchor": {"anchor_math": f"15000 / {gpu}"}}
    settled, pending = claim_checks.route([
        {"sentence_index": 0, "sentence": "", "type": claim_checks.QUOTE,
         "anaphoric": False, "claim": "the model ships in March"},
        {"sentence_index": 1, "sentence": "", "type": claim_checks.QUOTE,
         "anaphoric": False, "claim": "the model ships in April"},
        {"sentence_index": 2, "sentence": "", "type": claim_checks.FRAMING,
         "anaphoric": False, "claim": "Science, funded like a startup"},
        {"sentence_index": 3, "sentence": "", "type": claim_checks.JUDGMENT,
         "anaphoric": False, "claim": "this should worry NVIDIA"},
        {"sentence_index": 4, "sentence": "", "type": claim_checks.FACTUAL,
         "anaphoric": False, "claim": "it spans all tiers without extra fees"},
    ], slots)
    verdicts = {c["claim"]: c["verdict"] for c in settled}
    check("quote verbatim -> SUPPORTED",
          verdicts.get("the model ships in March") == claim_checks.SUPPORTED)
    check("quote altered -> REFUTED",
          verdicts.get("the model ships in April") == claim_checks.REFUTED)
    check("pure framing passes without a model call",
          verdicts.get("Science, funded like a startup") == claim_checks.SUPPORTED)
    check("judgment passes without a model call",
          verdicts.get("this should worry NVIDIA") == claim_checks.SUPPORTED)
    check("unsupported factual detail is routed to the model",
          len(pending) == 1 and "without extra fees" in pending[0]["claim"])

    print("\n--- draft + check on the fixture (replayed responses) ---")
    report = json.loads(runs.fixture_text("report.json"))
    recorded = json.loads(runs.fixture_text("responses.json"))

    replay = list(recorded)

    def stub_json_call(purpose, prompt, schema, *, run, temperature=None,
                       system=None, label=None):
        if not replay:
            raise AssertionError(
                f"selftest ran out of recorded responses at purpose {purpose!r} — "
                "the pipeline is making more calls than the fixture records")
        entry = replay.pop(0)
        assert entry["purpose"] == purpose, (
            f"selftest expected a {entry['purpose']!r} call, got {purpose!r} — "
            "the call sequence changed; re-record tests/fixtures/responses.json")
        return entry["response"]

    original = llm.json_call
    llm.json_call = stub_json_call
    stages.draft.llm.json_call = stub_json_call
    claim_checks.llm.json_call = stub_json_call
    try:
        with runs.temporary_root():
            run = runs.Run.new()
            run.write("report", report)
            stages.check.run(run)
            script = run.read("script")
        check("check passed the fixture script", script["passed"] is True)
        check("redrafting happened", script["attempts"] >= 1,
              f"attempts={script['attempts']}")
        check("claims were classified by type", bool(script["counts_by_type"]),
              str(script["counts_by_type"]))
        check("script retains a lede",
              any(b["slot"] == "lede" for b in script["beats"]))
        check("script retains a proof beat",
              any(b["slot"] not in ("lede", "tension") for b in script["beats"]))
        check("every recorded response was consumed", not replay,
              f"{len(replay)} unused")
        print(f"\n     {script['script_text'][:150]}...")
        print(f"     {len(script['beats'])} beats, {script['word_count']} words, "
              f"claims {script['counts_by_type']}")
    finally:
        llm.json_call = original
        stages.draft.llm.json_call = original
        claim_checks.llm.json_call = original

    print("\n--- erosion floor (no model) ---")
    first = [{"slot": s} for s in ("lede", "number", "number", "quote", "tension")]
    check("half the beats surviving passes",
          stages.check.erosion_problem(first, first[:3]) is None)
    check("fewer than half fails",
          stages.check.erosion_problem(first, first[:2]) is not None)
    check("losing the lede fails",
          stages.check.erosion_problem(first, [{"slot": "tension"}]) is not None)
    check("no proof beat fails",
          stages.check.erosion_problem(
              first, [{"slot": "lede"}, {"slot": "tension"}]) is not None)

    print()
    if failures:
        print(f"SELFTEST FAILED: {len(failures)} check(s): {'; '.join(failures)}")
        return 1
    print("SELFTEST PASSED — zero API calls made")
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
    parser.add_argument("--selftest", action="store_true",
                        help="run the pipeline against committed fixtures with "
                             "all model calls stubbed; zero API calls")
    args = parser.parse_args(argv)

    if args.costs:
        return _print_costs()
    if args.selftest:
        logging.basicConfig(level=logging.WARNING,
                            format="%(levelname)s %(name)s: %(message)s")
        return _selftest()

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
