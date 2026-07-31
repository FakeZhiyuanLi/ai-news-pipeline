"""check: verify the script and redraft until it holds. Writes script.json.

The one place a stage imports another stage: check.py calls draft.py for the
retry loop. That is deliberate — the retry is an implementation detail of
checking, not a pipeline edge.

Order of operations, cheapest first:
  1. Free Python checks (quotes, arithmetic, framing/judgment by type). No
     model call. These are also the only checks immune to shared model
     priors.
  2. ONE batched re-extraction against the source text for everything left,
     using the "check" purpose — GLM, a third family from draft's Kimi and
     report's DeepSeek. The checker does not share the priors that produced a
     bad claim, and re-extracting from source does not inherit whatever
     report's family overlooked.
  3. SUPPORTED claims are added to the report slots and the sentence STANDS.
     A writer reaching for something the notes lack is usually the extractor
     having under-collected, not the writer misbehaving.
  4. REFUTED claims go on a forbidden list and the script is redrafted.

EROSION FLOOR. Each redraft loses material. Comparing against a fixed
minimum would let a rich seven-beat story erode to three and still pass while
quietly becoming a worse video, so the floor is relative to the FIRST
attempt: at least half its beats must survive, and the lede plus one proof
beat must always remain.
"""

import logging

import config
import runs
from checks import claims as claim_checks
from stages import draft

log = logging.getLogger(__name__)


class CheckFailed(RuntimeError):
    """The script could not be made to hold. Carries the artifact written."""


def _proof_beats(beats: list[dict]) -> list[dict]:
    """Beats that actually support the hook. The closer restates the stake
    rather than proving it, so a script eroded down to hook + closer has no
    proof left and must not pass the floor on a technicality.

    Judged on KIND, not slot: which slot a beat drew from says nothing about
    what it does in the script — a hook is routinely built from a number, and
    a proof beat from the lede note. `quote` and `funnel` are proof.
    """
    proving = {"proof", "funnel", "quote"}
    return [b for b in beats
            if (b["kind"] in proving if b.get("kind")
                else b["slot"] not in ("lede", "tension"))]


def erosion_problem(first_beats: list[dict], final_beats: list[dict]) -> str | None:
    """Has redrafting hollowed the story out?"""
    floor = len(first_beats) / 2
    if len(final_beats) < floor:
        return (f"erosion floor: {len(final_beats)} of {len(first_beats)} beats "
                f"survived, fewer than half — the story has been checked into "
                "something thinner than it started")
    # Ask for the opening BEAT, not the lede SLOT. The hook is frequently
    # sourced from a number or the tension rather than the lede note — "5,000
    # labs asked Washington for AI money" is a hook built from a number — so
    # checking the slot rejected scripts that open perfectly well.
    slots = {b["slot"] for b in final_beats}
    kinds = {b.get("kind") for b in final_beats}
    if "hook" not in kinds and "lede" not in slots:
        return "the final script has no opening beat"
    if not _proof_beats(final_beats):
        return "the final script has no proof beat — nothing supports the lede"
    return None


def check_once(draft_artifact: dict, report: dict, run, *,
               attempt: int) -> dict:
    """Extract, route, verify. Returns the verdict for one draft.

    The caption is checked with the script, not after it. It is on screen for
    the whole video and is the part a viewer screenshots, so an unverified
    caption is a worse liability than an unverified spoken line — and it is
    free to include, since extraction is one batched call either way.
    """
    extracted = claim_checks.extract(
        draft_artifact["script_text"], run, label=f"check-extract-{attempt}",
        caption=draft_artifact.get("caption", ""))
    settled, pending = claim_checks.route(extracted, report["slots"])
    verified = claim_checks.verify_against_source(
        pending, report["source_text"], run, report_slots=report["slots"],
        label=f"check-verify-{attempt}") if pending else []

    all_claims = settled + verified
    refuted = [c for c in all_claims if c["verdict"] == claim_checks.REFUTED]
    unverified = [c for c in all_claims if c["verdict"] == claim_checks.UNVERIFIED]
    # A claim the checker could not reach a verdict on is not a pass.
    return {
        "claims": all_claims,
        "refuted": refuted + unverified,
        "supported": [c for c in all_claims
                      if c["verdict"] == claim_checks.SUPPORTED],
        "counts_by_type": claim_checks.counts_by_type(all_claims),
        "python_checked": len(settled),
        "model_checked": len(verified),
    }


def _absorb_supported(report: dict, supported: list[dict]) -> list[str]:
    """A SUPPORTED factual claim the notes lacked belongs in the notes. The
    sentence stands unchanged; the extractor is what under-collected."""
    added = []
    existing = " ".join(str(v) for v in report["slots"].values())
    for claim in supported:
        if claim["checked_by"] != "check model":
            continue
        if claim["type"] != claim_checks.FACTUAL:
            continue
        if claim["claim"].lower() in existing.lower():
            continue
        report["slots"].setdefault("numbers", []).append(claim["claim"])
        added.append(claim["claim"])
    return added


def run(run) -> None:
    report = run.read("report")
    first_draft = run.read("draft") if run.exists("draft") else \
        draft.generate(report, run, label="check-draft-1")

    forbidden: list[str] = []
    reached_for: list[dict] = []
    first_beats = first_draft["beats"]
    current = first_draft
    history = []

    for attempt in range(1, config.MAX_DRAFT_ATTEMPTS + 1):
        verdict = check_once(current, report, run, attempt=attempt)
        history.append({
            "attempt": attempt,
            "beats": len(current["beats"]),
            "counts_by_type": verdict["counts_by_type"],
            "refuted": [c["claim"] for c in verdict["refuted"]],
            "python_checked": verdict["python_checked"],
            "model_checked": verdict["model_checked"],
        })
        log.info("attempt %d: %d claims (%d settled in Python), %d refuted",
                 attempt, len(verdict["claims"]), verdict["python_checked"],
                 len(verdict["refuted"]))

        added = _absorb_supported(report, verdict["supported"])
        if added:
            log.info("absorbed %d supported claim(s) the report lacked: %s",
                     len(added), "; ".join(a[:50] for a in added))

        if not verdict["refuted"]:
            problem = erosion_problem(first_beats, current["beats"])
            if problem:
                raise _fail(run, report, current, history, reached_for, problem)
            # Nothing downstream can distinguish a funnel beat from a plain
            # proof beat without this, and the renderer needs to.
            kind_problems = draft.kind_problems(current["beats"])
            if kind_problems:
                raise _fail(run, report, current, history, reached_for,
                            "; ".join(kind_problems))
            run.write("script", {
                "story": report["story"],
                "caption": current.get("caption", ""),
                "script_text": current["script_text"],
                "tts_text": current["tts_text"],
                "beats": current["beats"],
                "beat_kinds": [b["kind"] for b in current["beats"]],
                "sentences": current["sentences"],
                "sentence_lengths": current["sentence_lengths"],
                "word_count": current["word_count"],
                "estimated_seconds": current["estimated_seconds"],
                "attempts": attempt,
                "claims": verdict["claims"],
                "counts_by_type": verdict["counts_by_type"],
                "absorbed_into_report": added,
                "history": history,
                "passed": True,
            })
            log.info("script passed on attempt %d: %d beats, %d words, ~%.1fs",
                     attempt, len(current["beats"]), current["word_count"],
                     current["estimated_seconds"])
            return

        for claim in verdict["refuted"]:
            forbidden.append(claim["claim"])
            reached_for.append({"claim": claim["claim"], "type": claim["type"],
                                "why": claim["why"], "attempt": attempt})

        if attempt == config.MAX_DRAFT_ATTEMPTS:
            break
        log.info("redrafting without %d refuted claim(s)", len(verdict["refuted"]))
        current = draft.generate(report, run, forbidden=forbidden,
                                 label=f"check-redraft-{attempt + 1}")

    raise _fail(run, report, current, history, reached_for,
                f"still refuting claims after {config.MAX_DRAFT_ATTEMPTS} attempts")


def _fail(run, report, current, history, reached_for, reason) -> CheckFailed:
    """Write the artifact before failing. The list of claims the writer kept
    reaching for is what report.py should have collected."""
    if not run.exists("script"):
        run.write("script", {
            "story": report["story"],
            "script_text": current["script_text"],
            "beats": current["beats"],
            "attempts": len(history),
            "history": history,
            "reached_for": reached_for,
            "failure": reason,
            "passed": False,
        })
    wanted = "\n".join(f"  - [{c['type']}] {c['claim']} ({c['why']})"
                       for c in reached_for)
    return CheckFailed(
        f"{reason}\n\nThe writer kept reaching for material the report lacks — "
        f"this is what extraction should have collected:\n{wanted}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="run the check stage standalone")
    parser.add_argument("--run-id", help="run to check within (default: latest)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    runs.load_env()
    current_run = runs.Run.resume(args.run_id) if args.run_id else runs.Run.latest()
    print(f"run {current_run.run_id}")
    if not current_run.exists("script"):
        run(current_run)
    script = current_run.read("script")
    print(f"\npassed: {script['passed']}  attempts: {script['attempts']}")
    print(f"claims by type: {script.get('counts_by_type', {})}")
    for claim in script.get("claims", []):
        print(f"  {claim['verdict']:10} {claim['type']:10} "
              f"({claim['checked_by']}) {claim['claim'][:60]}")
    print(f"\n{script['script_text']}")
