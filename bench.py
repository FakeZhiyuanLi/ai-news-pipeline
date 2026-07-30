"""The cheap iteration loop: regenerate drafts over existing reports and read
them side by side.

Narration is ~97% of marginal cost, so this loop must be structurally unable
to reach ElevenLabs — not merely uninclined to. _BlockNarration installs a
meta-path finder that makes the module unimportable for the duration, and any
attempt fails the bench run loudly.

find, pick, report, narrate and render do not run at all: report.json is
reused from each run directory. One sample proves nothing, so --variants
draws K drafts per report and prints them together; the column that matters
most is which report slots each draft actually used, because a draft using
two slots has collapsed regardless of how it reads.
"""

import argparse
import datetime as _dt
import logging
import sys

import config
import runs
from checks import prose
from stages import draft as draft_stage

log = logging.getLogger("bench")


class _BlockNarration:
    """Make importing a narration SDK impossible while a bench runs."""

    BLOCKED = ("elevenlabs",)

    def find_module(self, fullname, path=None):  # legacy protocol, harmless
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.BLOCKED:
            raise AssertionError(
                f"bench attempted to import {fullname!r} — narration is ~97% of "
                "marginal cost and must never run from the bench loop")
        return None

    def __enter__(self):
        for name in self.BLOCKED:
            if name in sys.modules:
                raise AssertionError(f"{name!r} was already imported before the bench")
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)


def _spend_usd(entries: list[dict]) -> float:
    return sum(entry.get("cost_usd", 0.0) for entry in entries)


def _costs_since(marker: int) -> list[dict]:
    try:
        lines = runs.read_jsonl(config.COSTS_FILE)
    except runs.MissingDataFileError:
        return []
    return lines[marker:]


def _variant_overlap(a: str, b: str) -> float:
    """Pairwise word overlap between two variants of the same story. Above
    ~90% the prompt is over-constrained and the variants tell us nothing."""
    wa, wb = prose.normalise(a), prose.normalise(b)
    if not wa or not wb:
        return 0.0
    from collections import Counter
    shared = sum((Counter(wa) & Counter(wb)).values())
    return 2 * shared / (len(wa) + len(wb))


def _render_draft(d: dict, label: str) -> list[str]:
    out = [f"### {label}",
           "",
           f"- words: **{d['word_count']}**  |  estimated: **{d['estimated_seconds']}s**"
           f"  |  beats: **{len(d['beats'])}**",
           f"- sentence lengths: **{d['sentence_lengths']}**"
           f"  |  attempts: {d.get('attempts', 1)}",
           f"- slots used: **{', '.join(d['slots_used'])}**"]
    if d["dropped_beats"]:
        out.append(f"- dropped (proof cap / MAX_SECONDS): "
                   f"{[b['slot'] for b in d['dropped_beats']]}")
    if d.get("banned_constructions"):
        out.append(f"- BANNED: {'; '.join(d['banned_constructions'])}")
    out += ["", "```", d["script_text"], "```", "", "beats:"]
    out += [f"{i}. `[{b['slot']}]` {b['sentence']}"
            for i, b in enumerate(d["beats"], 1)]
    tagged = [b for b in d["beats"] if b["tts_sentence"] != b["sentence"]]
    out += ["", f"tts tags on {len(tagged)}/{len(d['beats'])} sentences", ""]
    return out


def bench(n: int, variants: int, *, temperature: float, check: bool,
          run_ids: list[str] | None = None) -> str:
    if check:
        raise SystemExit("--check needs check.py, which does not exist yet")

    available = [r for r in runs.all_run_ids() if runs.Run.resume(r).exists("report")]
    if not available:
        raise SystemExit("no run directory has a report artifact yet — "
                         "run: python run.py --stage report")
    if run_ids:
        missing = [r for r in run_ids if r not in available]
        if missing:
            raise SystemExit(f"no report artifact in: {', '.join(missing)}")
        selected = run_ids
    else:
        selected = available[-n:]
    log.info("benching %d report(s) x %d variant(s)", len(selected), variants)

    marker = len(_costs_since(0))
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime(config.RUN_TIMESTAMP_FORMAT)
    lines = [f"# bench {stamp}", "",
             f"{len(selected)} reports x {variants} variants, "
             f"temperature {temperature}, draft model "
             f"{config.MODELS['draft'][0].split('/')[-1]}", ""]

    slot_usage: dict[str, int] = {}
    overlaps: list[float] = []
    drafts_made = 0
    for run_id in selected:
        run = runs.Run.resume(run_id)
        report = run.read("report")
        available = [s for s in config.REPORT_SLOTS if report["slots"].get(s)]
        lines += ["---", "",
                  f"## {report['story']['title']}",
                  "",
                  f"`{run_id}`  |  slots available in report: **{', '.join(available)}**",
                  ""]
        print(f"\n=== {report['story']['title']}")
        print(f"    report slots: {', '.join(available)}")
        story_scripts = []
        for k in range(1, variants + 1):
            try:
                d = draft_stage.generate(report, run, temperature=temperature,
                                         label=f"bench-draft-{k}")
            except Exception as e:
                lines += [f"### variant {k}", "", f"FAILED: {e.__class__.__name__}: {e}", ""]
                print(f"    variant {k}: FAILED {e.__class__.__name__}: {e}")
                continue
            drafts_made += 1
            story_scripts.append(d["script_text"])
            for slot in d["slots_used"]:
                slot_usage[slot] = slot_usage.get(slot, 0) + 1
            lines += _render_draft(d, f"variant {k}")
            print(f"    variant {k}: {len(d['beats'])} beats, {d['word_count']}w, "
                  f"~{d['estimated_seconds']}s, lengths {d['sentence_lengths']}, "
                  f"slots: {', '.join(d['slots_used'])}"
                  + (f"  BANNED: {'; '.join(d['banned_constructions'])}"
                     if d.get("banned_constructions") else ""))
            print(f"      {d['script_text']}")

        # Variants that agree too closely are not telling us anything: the
        # prompt has left only one possible output.
        for i in range(len(story_scripts)):
            for j in range(i + 1, len(story_scripts)):
                overlap = _variant_overlap(story_scripts[i], story_scripts[j])
                overlaps.append(overlap)
                flag = "  OVER-CONSTRAINED" if overlap > 0.90 else ""
                note = (f"variant {i + 1} vs {j + 1}: "
                        f"{overlap:.0%} word overlap{flag}")
                lines += [f"- {note}", ""]
                print(f"    {note}")

    spend = _spend_usd(_costs_since(marker))
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
    summary = [f"- drafts: {drafts_made}",
               f"- slot usage across drafts: "
               f"{', '.join(f'{s}={n}' for s, n in sorted(slot_usage.items())) or 'none'}",
               f"- mean variant overlap: {mean_overlap:.0%}"
               + ("  (over ~90% means the prompt is over-constrained)"
                  if mean_overlap > 0.90 else ""),
               f"- spend: ${spend:.5f}"]
    lines += ["---", "", "## totals", ""] + summary + [""]
    print("\n" + "\n".join(summary))

    assert spend < config.BENCH_MAX_SPEND_USD, (
        f"bench spent ${spend:.5f}, over the ${config.BENCH_MAX_SPEND_USD} ceiling")

    path = runs.write_text_file(config.BENCH_DIR, f"{stamp}.md", "\n".join(lines))
    print(f"\nwrote {path}")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.py", description="regenerate drafts over existing reports")
    parser.add_argument("--bench", type=int, default=3, metavar="N",
                        help="how many existing run directories to draft over")
    parser.add_argument("--variants", type=int, default=2, metavar="K",
                        help="drafts per report; one sample proves nothing")
    parser.add_argument("--run-ids", nargs="+", metavar="ID",
                        help="bench these specific runs instead of the last N")
    parser.add_argument("--draft-model", metavar="SLUG",
                        help="override the model used for drafting, to compare "
                             "writers over the same reports")
    parser.add_argument("--draft-effort", metavar="EFFORT",
                        help="override reasoning effort for drafting")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--check", action="store_true",
                        help="also check each draft (needs check.py; off by default "
                             "because the strong model at 5 RPM makes this slow)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    # Model selection belongs to config.MODELS; the bench may point the
    # "draft" purpose somewhere else for one run to compare writers.
    if args.draft_model or args.draft_effort:
        model, effort = config.MODELS["draft"]
        config.MODELS["draft"] = (args.draft_model or model,
                                  args.draft_effort or effort)
        log.info("drafting with %s (effort %s)", *config.MODELS["draft"])

    with _BlockNarration():
        bench(args.bench, args.variants, temperature=args.temperature,
              check=args.check, run_ids=args.run_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
