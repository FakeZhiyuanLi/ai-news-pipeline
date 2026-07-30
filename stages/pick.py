"""pick: choose the one story this run will cover. Writes the story artifact.

Gates run in order; the first two are free and happen BEFORE any model call:

  1. AGE     — anything older than MAX_STORY_AGE_HOURS is dropped. Age is a
               gate, not a score. If nothing survives, the run FAILS naming
               the newest candidate; the window does not widen.
  2. COVERED — anything already in the ledger by URL is dropped. A related
               title (find_related) is NOT dropped: it is flagged
               is_update=True with the prior slots attached.
  3. RANK    — one "rank" model call scoring all survivors together, title
               plus the first ~400 chars of summary each. Never one call per
               candidate.

Final order: model score, HN signal_score as tiebreak. The chosen story is
the top-level content of story.json; the full table is under "ranking".
"""

import datetime as _dt
import logging

import config
import ledger
import llm

log = logging.getLogger(__name__)

_RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "reason"],
            },
        },
    },
    "required": ["scores"],
}

_RANK_PROMPT = """\
You are ranking AI news candidates for a short daily video. Exactly one will
be chosen. Score each candidate from 0 to {max_score} on how strong a story
it is TODAY:

- Does this change what someone can do today (a real, available release)?
- Are there hard numbers: benchmarks, prices, counts, dates?
- Is it an actual release, not an announcement of a future announcement?
- Is there controversy or a surprising result?

Penalise heavily: patch notes, minor version bumps, pure marketing with no
numbers behind it.

Score every candidate, using exactly the indices given.

Candidates:
{candidates}
"""


def _age_hours(published_iso: str, now: _dt.datetime) -> float:
    published = _dt.datetime.fromisoformat(published_iso)
    return (now - published).total_seconds() / 3600


def _gate_age(candidates: list[dict], now: _dt.datetime) -> list[dict]:
    dated = [c for c in candidates if c["published"]]
    if not dated:
        raise RuntimeError("no candidate carries a published date; "
                           "freshness cannot be established — failing the run")
    fresh = []
    for cand in dated:
        age = _age_hours(cand["published"], now)
        if age <= config.MAX_STORY_AGE_HOURS:
            fresh.append({**cand, "age_hours": round(age, 1)})
    if not fresh:
        newest = min(dated, key=lambda c: _age_hours(c["published"], now))
        raise RuntimeError(
            f"no candidate younger than {config.MAX_STORY_AGE_HOURS}h — newest is "
            f"{newest['title']!r} at {_age_hours(newest['published'], now):.0f}h old. "
            "The age window does not widen.")
    log.info("age gate: %d of %d candidates fresh", len(fresh), len(candidates))
    return fresh


def _gate_covered(candidates: list[dict]) -> list[dict]:
    survivors = []
    for cand in candidates:
        if ledger.is_covered(cand["url"]):
            log.info("covered, dropping: %s", cand["title"])
            continue
        related = ledger.find_related(cand["title"])
        cand["is_update"] = bool(related)
        cand["prior_slots"] = sorted({s for r in related for s in (r["slots"] or [])})
        if related:
            log.info("update to prior coverage (%s): %s",
                     ", ".join(r["run_id"] for r in related), cand["title"])
        survivors.append(cand)
    if not survivors:
        raise RuntimeError("every fresh candidate is already in the ledger — "
                           "nothing new to cover today")
    return survivors


def _rank(survivors: list[dict], run) -> list[dict]:
    lines = []
    for i, cand in enumerate(survivors):
        summary = cand["summary"][:config.RANK_SUMMARY_CHARS]
        lines.append(f"[{i}] {cand['title']}\n    {summary}")
    prompt = _RANK_PROMPT.format(max_score=config.RANK_MAX_SCORE,
                                 candidates="\n".join(lines))
    result = llm.json_call("rank", prompt, _RANK_SCHEMA, run=run)

    by_index = {s["index"]: s for s in result["scores"]}
    missing = [i for i in range(len(survivors)) if i not in by_index]
    if missing:
        raise RuntimeError(f"ranker omitted candidate indices {missing}; "
                           "refusing to guess their scores")
    for i, cand in enumerate(survivors):
        cand["model_score"] = min(max(float(by_index[i]["score"]), 0.0),
                                  float(config.RANK_MAX_SCORE))
        cand["model_reason"] = by_index[i]["reason"]
    return sorted(survivors,
                  key=lambda c: (c["model_score"], c["signal_score"]),
                  reverse=True)


def run(run) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    candidates = run.read("candidates")["candidates"]
    fresh = _gate_age(candidates, now)
    survivors = _gate_covered(fresh)
    ranked = _rank(survivors, run)
    chosen = ranked[0]
    log.info("picked (model %.1f, signal %.2f): %s",
             chosen["model_score"], chosen["signal_score"], chosen["title"])
    run.write("story", {
        **chosen,
        "picked_at": now.isoformat(timespec="seconds"),
        "ranking": ranked,
    })


def _print_table(story: dict, top_n: int) -> None:
    print(f"{'':2} {'age(h)':>7} {'signal':>7} {'model':>6}  title")
    for i, cand in enumerate(story["ranking"][:top_n]):
        marker = "*" if i == 0 else " "
        update = " [update]" if cand.get("is_update") else ""
        print(f"{marker:2} {cand['age_hours']:>7.1f} {cand['signal_score']:>7.2f} "
              f"{cand['model_score']:>6.1f}  {cand['title'][:70]}{update}")
    print(f"\npicked: {story['title']}\n  {story['url']}\n  model said: {story['model_reason']}")


if __name__ == "__main__":
    import argparse

    import runs

    parser = argparse.ArgumentParser(description="run the pick stage standalone")
    parser.add_argument("--run-id", help="run to pick within (default: latest)")
    parser.add_argument("--top-n", type=int, default=10, metavar="N",
                        help="show the top N candidates side by side")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    current = runs.Run.resume(args.run_id) if args.run_id else runs.Run.latest()
    print(f"run {current.run_id}")
    if not current.exists("story"):
        run(current)
    _print_table(current.read("story"), args.top_n)
