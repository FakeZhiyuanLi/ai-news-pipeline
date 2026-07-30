"""draft: write the script. Writes draft.json.

BEATS, NOT LENGTH. Each populated report slot is at most one beat; one
sentence per beat; cover once and stop. A three-beat story is a
three-sentence script and that is a complete, correct result. The writer is
never shown a target duration or a sentence count — estimated_seconds is
computed here from the word count and is a measurement, not a goal.

Two text fields, always: script_text is clean and is the source of truth for
subtitles, checking and visual matching; tts_text carries ElevenLabs v3 audio
tags. Stripping bracketed spans from tts_text must yield script_text exactly,
and that is asserted, not hoped for.
"""

import logging
import random
import re

import config
import llm

log = logging.getLogger(__name__)

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string",
                             "enum": ["lede", "number", "quote", "tension",
                                      "scale_anchor", "so_what"]},
                    "sentence": {"type": "string"},
                    "tts_sentence": {"type": "string"},
                },
                "required": ["slot", "sentence", "tts_sentence"],
            },
        },
    },
    "required": ["beats"],
}

_PROMPT = """\
You are writing the narration for a short vertical news video. Write in
BEATS. A beat is one idea from the reporting notes below, expressed in
exactly one sentence.

Order the beats:
  1. the lede — the most consequential thing, and the story's way in
  2. why it matters — the "why it matters" note, in your own words, as the
     second sentence. This is the beat that makes someone keep watching, so
     it is required whenever the notes carry one. Use slot "so_what".
  3. proof beats — the ranked numbers, and the quote if there is one, in the
     order given; the scale anchor if present belongs beside its number
  4. the catch — the tension, last

Rules that decide whether this script is correct:

- One sentence per beat. Cover each beat once, then stop. Three beats means a
  three-sentence script and that is a complete, correct result.
- Never pad. Never restate a fact in different words to fill space. Never add
  a beat the notes do not support.
- Where there is no tension in the notes, the script simply ends after the
  last proof beat. Do not invent a catch.
- There is no target length. Do not aim for any number of sentences.

The notes are reporting material, not a list to read out. You are choosing
what to tell someone in a hurry:

- AT MOST {max_proof} proof beats, and fewer is usually better. The notes are
  ranked, so the ones worth using come first — take what earns its place and
  stop. Leaving good material unused is normal and correct.
- Include a number only if a viewer would repeat it to someone else. A
  measurement threshold, an internal tool name, a unit only a specialist
  tracks, or a figure that merely sizes the study is detail, not a beat —
  drop it, even though it is in the notes.
- If two numbers make the same point, use the stronger one only.

You may exercise judgment in exactly three places, and nowhere else:
  1. WHICH FACT LEADS — e.g. leading with the price cut rather than the
     benchmark, because the price is what changes someone's decision today.
  2. WHAT GETS FRAMED AS THE CATCH — e.g. framing "US-only at launch" as the
     limitation that matters, rather than the unshipped SDK.
  3. SENTENCE RHYTHM — e.g. a short sentence after a long one for emphasis.
Never exercise judgment about the world: do not add significance, prediction,
praise or blame that the notes do not state. "This is the part that should
worry their competitors" is framing and is allowed only if the notes state
the competitive stake; "this will fail" is not.

Never write:
- a company name followed by announced, unveiled, released or launched. The
  release is not the news; what the thing does or costs is. Write "Opus 5
  costs five dollars per million input tokens", never "Anthropic released
  Opus 5". This applies even when the release IS the story — lead with the
  substance and let the company appear where it belongs.
- the phrase "designed to"
- any superlative that is not inside a quotation from a named person
- sign-offs, calls to action, or "let's dive in"
- the words "quote" and "unquote" as narration. Attribute a quotation the way
  a person speaking aloud would: "As Keri Powell put it, ..." — never "quote,
  ... unquote".
- a speaker's full title in front of their name. "As Keri Powell, an attorney
  at the Southern Environmental Law Center, put it" is speakable; "As
  Atlanta-based attorney at the environmental legal advocacy group Southern
  Environmental Law Center Keri Powell put it" is not.

Read every sentence back as speech before you emit it. It must be
grammatical and it must be sayable in one breath by a narrator.

Two versions of every sentence:
- sentence: clean text. No brackets, no tags. This is what appears as
  subtitles.
- tts_sentence: the same words, character for character, optionally with ONE
  audio tag inserted. Removing every [bracketed] span from tts_sentence must
  leave exactly the clean sentence.
  Permitted tags, and no others: {tags}
  Use tags sparingly — most sentences should have none, and the opening
  sentence must have none.

Open with this hook, as its own first beat with slot "lede" merged into it —
that is, begin the first sentence with the hook text exactly as written:
  {hook}

Reporting notes:
{notes}
"""


# --- sentence splitting ----------------------------------------------------

# A period is a boundary only when whitespace follows it. A period with no
# whitespace after it is inside an identifier or a number: "torch.compile"
# and "3.5" must not split, and scripts in this niche legitimately begin
# sentences with torch.compile, npm, pip — so capitalisation is never used.
_ABBREVIATIONS = ("u.s.", "e.g.", "i.e.", "inc.", "vs.", "dr.", "mr.", "ms.",
                  "st.", "jr.", "sr.", "etc.", "fig.", "approx.")

# A terminator, any closing quote/bracket that trails it, then whitespace.
# The boundary falls AFTER the closing mark so a sentence-final quotation
# stays with the sentence it closes.
_BOUNDARY_RE = re.compile(r'[.!?]["\'”’)\]]*(?=\s)')


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        end = match.end()
        candidate = text[start:end]
        last_word = candidate.split()[-1].lower() if candidate.split() else ""
        if last_word in _ABBREVIATIONS:
            continue  # "in the U.S. last year" is not a boundary
        sentences.append(candidate)
        start = end
    if text[start:].strip():
        sentences.append(text[start:])
    return [s.strip() for s in sentences if s.strip()]


# --- tts tags --------------------------------------------------------------

_BRACKET_RE = re.compile(r"\[[^\]]*\]")


def strip_tags(tts_text: str) -> str:
    """Remove every bracketed span and normalise the whitespace it leaves."""
    return re.sub(r"\s+", " ", _BRACKET_RE.sub(" ", tts_text)).strip()


def sanitise_tts(tts_sentence: str, clean_sentence: str) -> str:
    """Keep allowlisted tags, drop everything else bracketed, and cap the
    count. Returns a tts sentence that provably strips back to clean."""
    kept = []

    def keep(match):
        tag = match.group(0).lower()
        if tag in config.AUDIO_TAGS and len(kept) < config.MAX_TAGS_PER_SENTENCE:
            kept.append(tag)
            return tag
        return " "

    candidate = re.sub(r"\s+", " ", _BRACKET_RE.sub(keep, tts_sentence)).strip()
    if strip_tags(candidate) != clean_sentence:
        # The model altered the words around the tag; the clean text is the
        # source of truth, so fall back to it rather than trusting the tags.
        log.warning("tts sentence did not strip back to clean text; dropping tags: %r",
                    tts_sentence)
        return clean_sentence
    return candidate


# --- beat assembly ---------------------------------------------------------

def _notes_for_prompt(slots: dict) -> str:
    lines = []
    if slots.get("lede"):
        lines.append(f"lede: {slots['lede']}")
    if slots.get("so_what"):
        lines.append(f"why it matters (required as the second beat): "
                     f"{slots['so_what']}")
    for i, number in enumerate(slots.get("numbers") or [], 1):
        lines.append(f"number {i} (ranked): {number}")
    anchor = slots.get("scale_anchor")
    if anchor:
        lines.append(f"scale anchor: {anchor['statement']}")
    quote = slots.get("quote")
    if quote:
        lines.append(f'quote from {quote["name"]}, {quote["role"]}: "{quote["text"]}"')
    if slots.get("tension"):
        lines.append(f"tension (the catch): {slots['tension']}")
    return "\n".join(lines)


def pick_hook(run_id: str) -> str:
    return random.Random(run_id).choice(config.HOOKS)


def estimate_seconds(text: str) -> float:
    return round(len(text.split()) / config.WORDS_PER_SECOND, 1)


# Beats that carry the story and are never dropped: the way in, the reason to
# care, and the catch.
_STRUCTURAL_SLOTS = ("lede", "so_what", "tension")


def _truncate_to_budget(beats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop from the bottom of the ranked proof beats — never the lede, the
    so_what or the catch — until the script is within both MAX_PROOF_BEATS
    and MAX_SECONDS.

    The proof cap is enforced here as well as asked for in the prompt: the
    notes are ranked, so dropping from the bottom keeps the beats that earned
    their place, and a model that recites the whole list still yields a
    watchable script."""
    kept, dropped = list(beats), []

    def proof_indices():
        return [i for i, b in enumerate(kept) if b["slot"] not in _STRUCTURAL_SLOTS]

    while len(proof_indices()) > config.MAX_PROOF_BEATS:
        dropped.append(kept.pop(proof_indices()[-1]))
    while estimate_seconds(" ".join(b["sentence"] for b in kept)) > config.MAX_SECONDS:
        droppable = proof_indices()
        if not droppable:
            break  # structure alone exceeds the cap: report it, don't mangle it
        dropped.append(kept.pop(droppable[-1]))
    return kept, dropped


def find_banned(script_text: str) -> list[str]:
    """Wire-copy constructions, checked deterministically. Reported on the
    artifact rather than raised: this stage is unconstrained by design, and
    check.py decides what is fatal."""
    found = [label for pattern, label in config.BANNED_PATTERNS
             if re.search(pattern, script_text, re.IGNORECASE)]
    outside_quotes = re.sub(r'"[^"]*"', " ", script_text)
    if re.search(config.SUPERLATIVE_PATTERN, outside_quotes, re.IGNORECASE):
        found.append("superlative outside an attributed quotation")
    return found


def build_draft(slots: dict, beats: list[dict], hook: str) -> dict:
    """Assemble the artifact from raw model beats. Pure — no IO, no model
    call — so bench.py and the tests can exercise it directly."""
    clean_beats = []
    for beat in beats:
        # The writer sometimes puts an audio tag in the clean field too.
        # script_text is the source of truth for subtitles and checking and
        # must never contain a bracketed span, so strip it here rather than
        # trusting the field to be clean.
        sentence = strip_tags(beat["sentence"])
        if not sentence:
            continue
        clean_beats.append({
            "slot": beat["slot"],
            "sentence": sentence,
            "tts_sentence": sanitise_tts(beat["tts_sentence"], sentence),
        })
    if not clean_beats:
        raise RuntimeError("writer returned no usable beats")

    kept, dropped = _truncate_to_budget(clean_beats)
    if dropped:
        log.info("dropped %d proof beat(s) (cap %d, then MAX_SECONDS): %s",
                 len(dropped), config.MAX_PROOF_BEATS, [b["slot"] for b in dropped])

    script_text = " ".join(b["sentence"] for b in kept)
    tts_text = " ".join(b["tts_sentence"] for b in kept)

    # Two text fields, always — and the invariant that ties them together.
    if strip_tags(tts_text) != script_text:
        raise RuntimeError(
            "tts_text does not strip to script_text:\n"
            f"  stripped: {strip_tags(tts_text)!r}\n"
            f"  script:   {script_text!r}")

    sentences = split_sentences(script_text)
    banned = find_banned(script_text)
    if banned:
        log.warning("banned constructions present: %s", "; ".join(banned))
    return {
        "hook": hook,
        "script_text": script_text,
        "tts_text": tts_text,
        "beats": kept,
        "dropped_beats": dropped,
        "slots_used": sorted({b["slot"] for b in kept}),
        "sentences": sentences,
        "word_count": len(script_text.split()),
        "estimated_seconds": estimate_seconds(script_text),
        "banned_constructions": banned,
    }


def generate(report: dict, run, *, temperature: float = 0.7,
             label: str | None = None) -> dict:
    """One "draft" model call over the full report slots. Returns the draft
    artifact without writing it — check.py's retry loop calls this directly.
    label only names the transcript, for callers drafting more than once."""
    slots = report["slots"]
    hook = pick_hook(run.run_id)
    prompt = _PROMPT.format(tags=", ".join(config.AUDIO_TAGS), hook=hook,
                            max_proof=config.MAX_PROOF_BEATS,
                            notes=_notes_for_prompt(slots))
    result = llm.json_call("draft", prompt, _DRAFT_SCHEMA, run=run,
                           temperature=temperature, label=label)
    draft = build_draft(slots, result["beats"], hook)
    draft["story"] = report["story"]
    return draft


def run(run) -> None:
    report = run.read("report")
    draft = generate(report, run)
    run.write("draft", draft)
    log.info("%d beats, %d words, ~%.1fs, slots: %s",
             len(draft["beats"]), draft["word_count"], draft["estimated_seconds"],
             ", ".join(draft["slots_used"]))


if __name__ == "__main__":
    import argparse

    import runs

    parser = argparse.ArgumentParser(description="run the draft stage standalone")
    parser.add_argument("--run-id", help="run to draft within (default: latest)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    # Splitter behaviour that CLAUDE.md says has bitten this project before.
    assert split_sentences("B200. torch.compile lifts speed by 30%.") == \
        ["B200.", "torch.compile lifts speed by 30%."]
    assert split_sentences("Costs fell 3.5 percent in the U.S. last year.") == \
        ["Costs fell 3.5 percent in the U.S. last year."]
    assert split_sentences('She said "no." Then she left.') == \
        ['She said "no."', "Then she left."]
    assert split_sentences("It costs $5.25 per token. npm install works.") == \
        ["It costs $5.25 per token.", "npm install works."]
    assert strip_tags("[excited] It shipped. [pause] Finally.") == "It shipped. Finally."
    print("splitter and tag-stripper OK")

    current = runs.Run.resume(args.run_id) if args.run_id else runs.Run.latest()
    print(f"run {current.run_id}")
    if not current.exists("draft"):
        run(current)
    draft = current.read("draft")
    print(f"\nhook: {draft['hook']}")
    for beat in draft["beats"]:
        print(f"  [{beat['slot']}] {beat['sentence']}")
    print(f"\nscript: {draft['script_text']}")
    print(f"tts:    {draft['tts_text']}")
    print(f"\n{draft['word_count']} words, ~{draft['estimated_seconds']}s, "
          f"slots used: {', '.join(draft['slots_used'])}")
