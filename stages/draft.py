"""draft: write the script. Writes draft.json.

NOTES ARE NOT COPY. The report slots are notes — complete-sentence summaries
written for an editor to read. A script is copy: spoken aloud, front-loaded,
short. Everything here exists to stop the writer treating the one as a draft
of the other, because when it does, every symptom follows at once — 29-word
sentences, no rhythm, identical variants, a flat second beat.

Two gates enforce the difference in Python, because "write it in your own
words" is unenforceable as an instruction:

  - checks.prose.find_passthrough — reject a sentence sharing 8+ consecutive
    words with the notes (the verbatim quote is exempt, and so are runs of
    only names and numbers).
  - checks.prose.rhythm_problems — reject uniform pacing: nothing over 20
    words, at least one under 8, not everything clustered at the mean.

Both are backstops set generously. If they fire constantly, the prompt has
stopped framing the task generatively and the prompt is what needs fixing.

BEATS, NOT LENGTH. Each populated slot is at most one beat; cover once and
stop. so_what is NOT a beat — it is context that shapes how the lede is
framed and which number leads. Emitted as its own sentence it was the
flattest line in every draft.

Two text fields, always: script_text is clean and is the source of truth for
subtitles, checking and visual matching; tts_text carries ElevenLabs v3 audio
tags. Stripping bracketed spans from tts_text must yield script_text exactly,
and that is asserted, not hoped for.
"""

import logging
import re

import config
import llm
from checks import numbers as number_checks
from checks import prose

log = logging.getLogger(__name__)

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # so_what is deliberately absent: it is context, never a beat.
                    "slot": {"type": "string",
                             "enum": ["lede", "number", "quote", "tension",
                                      "scale_anchor"]},
                    "sentence": {"type": "string"},
                    "tts_sentence": {"type": "string"},
                },
                "required": ["slot", "sentence", "tts_sentence"],
            },
        },
    },
    "required": ["beats"],
}

_SYSTEM = """\
You write narration for short vertical news videos. You are given reporting
notes and you write copy from them.

Notes and copy are different registers, and this is the whole job:

  NOTES are complete-sentence summaries, written for an editor to read on a
  page. They are dense, they carry every qualifier, and they run long.

  COPY is spoken aloud to someone who has not decided to keep watching. It is
  front-loaded, concrete, and short. It has rhythm — a three-word sentence
  after a long one is how speech breathes.

Write from the facts in the notes as though you had heard them from a
colleague and never saw the page. Do not edit the notes. Do not paraphrase
them clause by clause. Do not swap synonyms into their sentence structure —
"relocating governmental scientific monies" for "shifting public science
funding" is worse than the original, not better. Start from what happened and
say it the way a person would say it.

Here is the same story as notes, and as copy.

NOTES:
  lede: "The Trump administration allocated $5 billion to AI-focused research
    grants while proposing a policy plan that shifts federal science funding
    away from universities toward individual researchers and private
    companies."
  numbers: "$5 billion for Genesis Mission grants" / "278 projects selected" /
    "over 5,000 grant applications submitted"
  quote: "You cannot use venture capital to force breakthroughs while
    virtually already selling the patent." — Claes de Vreese, AI policy
    professor
  tension: "The policy manifesto contains contradictions, such as cutting life
    sciences funding while boosting foundational biological research, without
    defining the boundary between the two."

COPY:
  Washington just put $5 billion into AI research. 5,000 labs applied. 278 got
  it. And the money skips universities entirely — it goes to individual
  researchers and private companies. Science, funded like a startup portfolio.
  AI policy professor Claes de Vreese: "You cannot use venture capital to
  force breakthroughs while virtually already selling the patent." The plan
  also cuts life sciences while boosting foundational biology, without saying
  where one ends and the other begins.

Study what changed. The lede went from 31 words to 9. The two funnel numbers
were paired so the attrition itself carries the point. The "why it matters"
never became its own sentence — it became the framing, "funded like a startup
portfolio". Sentence lengths run 9, 3, 3, 15, 6, 24, 20. Nothing was invented:
every fact traces back to a note.

That is register, not a template. Do not imitate its wording.
"""

_PROMPT = """\
Write the narration for this story.

STRUCTURE
  1. The lede IS the first sentence: the most surprising concrete fact, under
     12 words. No preamble, no "here's what's new", no throat-clearing. The
     viewer decided whether to stay before you finished the sentence.
  2. Proof beats, from the ranked numbers and the quote if there is one.
     At most {max_proof}, fewer is usually better, in the order given.
  3. The catch, from the tension note, last.
  If there is no tension note, the script simply ends after the last proof
  beat. Never invent a catch.

RHYTHM — this is checked, and a script that fails is sent back
  - No sentence over {max_words} words. The sentence carrying the quotation is
    exempt: reproduce the quote in full, never trim it to fit.
  - At least one sentence of FIVE WORDS OR FEWER. Not seven, not eight —
    genuinely short. "278 got it." A short line after a long one is the
    cheapest way to sound like a person.
  - Vary the lengths wildly. Put a 3-word sentence next to a 15-word one.
    Sentences all landing near the same length is the strongest tell that a
    machine wrote them.

WRITING FROM THE NOTES — also checked
  - No sentence may reuse {max_run} or more consecutive words from the notes.
    Names, numbers and the quote are exempt.
  - This is not a filter to route around with synonyms. Write what happened.

NUMBERS
  - Digits and symbols, always: "28%", "$5 billion", "278" — never "twenty-
    eight percent". It is punchier on screen and reads correctly aloud.
  - Where two numbers describe the same funnel — 5,000 applied, 278 funded —
    state both together. The ratio is the story; either number alone is inert.

THE QUOTE, if there is one
  - Reproduce the quoted words EXACTLY as given, character for character,
    inside quotation marks. This is verified by substring match.
  - Name the speaker and compress their role to a few words: "AI policy
    professor Claes de Vreese", not "professor of AI and society at the
    University of Amsterdam in the Netherlands Claes de Vreese".
  - Vary how you attribute. A colon, "as X put it", "X calls it" — not the
    same construction every time. Never write the words "quote" or "unquote".

NEVER
  - a company name followed by announced, unveiled, released or launched. The
    release is not the news; what the thing does or costs is.
  - "designed to"
  - a superlative that is not inside the quotation
  - sign-offs, calls to action, "let's dive in"

TWO VERSIONS OF EVERY SENTENCE
  - sentence: clean text. No brackets, no tags. This becomes the subtitles.
  - tts_sentence: the same words, character for character, optionally with ONE
    audio tag. Removing every [bracketed] span must leave the clean sentence
    exactly. Permitted tags and no others: {tags}
    Most sentences should have none, and the first must have none.

{context}
NOTES
{notes}
"""

_RETRY_PREAMBLE = """\
The previous attempt was rejected. {reason}

{detail}

Write it again from the facts. Do not adjust the rejected sentences word by
word — that produces stilted synonym-swapping. Start over from what happened
and say it plainly.

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


# --- notes -----------------------------------------------------------------

def usable_anchor(slots: dict) -> dict | None:
    """The anchor, but only if its arithmetic lands in a graspable range.
    "$5bn is 75,075 average salaries" is a second incomprehensible number."""
    anchor = slots.get("scale_anchor")
    ok, reason = number_checks.anchor_is_useful(anchor)
    if anchor and not ok:
        log.info("skipping scale anchor: %s", reason)
    return anchor if ok else None


def _notes_for_prompt(slots: dict) -> str:
    lines = []
    if slots.get("lede"):
        lines.append(f"what happened: {slots['lede']}")
    for i, number in enumerate(slots.get("numbers") or [], 1):
        lines.append(f"number {i} (ranked): {number}")
    anchor = usable_anchor(slots)
    if anchor:
        lines.append(f"scale anchor: {anchor['statement']}")
    quote = slots.get("quote")
    if quote:
        lines.append(f'quote — reproduce EXACTLY, in quotation marks — '
                     f'{quote["name"]}, {quote["role"]}: "{quote["text"]}"')
    if slots.get("tension"):
        lines.append(f"the catch: {slots['tension']}")
    return "\n".join(lines)


def _context_for_prompt(slots: dict, story: dict) -> str:
    """so_what is context, never a beat: it shapes how the lede is framed and
    which number leads, and must not become a sentence of its own."""
    parts = []
    if slots.get("so_what"):
        parts.append(
            "WHY THIS MATTERS — this is context, NOT a sentence to write. Let it\n"
            "decide how you frame the lede and which number you lead with. Never\n"
            "state it directly; if it belongs in the script at all it belongs as\n"
            f"the angle, not as a line:\n  {slots['so_what']}\n")
    if story.get("is_update") and story.get("prior_slots"):
        parts.append(
            f"This updates earlier coverage of the same story (already covered: "
            f"{', '.join(story['prior_slots'])}). A contrast the notes support is\n"
            "reporting; do not re-lead with what was covered before.\n")
    return "\n".join(parts)


def notes_text(slots: dict) -> str:
    """Everything the overlap gate compares against: the non-quote notes."""
    parts = [slots.get("lede"), slots.get("so_what"), slots.get("tension")]
    parts += list(slots.get("numbers") or [])
    anchor = slots.get("scale_anchor")
    if anchor:
        parts.append(anchor.get("statement"))
    return " ".join(p for p in parts if p)


def _proper_nouns(slots: dict) -> set[str]:
    """Capitalised words from the notes: unavoidable in any retelling, so a
    run made only of these does not count as passthrough."""
    names = set()
    for chunk in [notes_text(slots), (slots.get("quote") or {}).get("name", "")]:
        names.update(re.findall(r"\b[A-Z][a-zA-Z]+\b", chunk or ""))
    return names


# --- beat assembly ---------------------------------------------------------

def find_banned(script_text: str) -> list[str]:
    """Wire-copy constructions, checked deterministically."""
    found = [label for pattern, label in config.BANNED_PATTERNS
             if re.search(pattern, script_text, re.IGNORECASE)]
    outside_quotes = re.sub(r'"[^"]*"', " ", script_text)
    if re.search(config.SUPERLATIVE_PATTERN, outside_quotes, re.IGNORECASE):
        found.append("superlative outside an attributed quotation")
    return found


def estimate_seconds(text: str) -> float:
    return round(len(text.split()) / config.WORDS_PER_SECOND, 1)


# Never dropped: the way in, the catch, and the quote. A named human saying
# something in their own words is the most valuable proof a script has, and
# the cap must not spend it on a fourth number.
_STRUCTURAL_SLOTS = ("lede", "tension", "quote")


def _truncate_to_budget(beats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop from the bottom of the ranked proof beats — never the lede, the
    catch, or the quote — until the script fits MAX_PROOF_BEATS and
    MAX_SECONDS."""
    kept, dropped = list(beats), []

    def proof_indices():
        return [i for i, b in enumerate(kept) if b["slot"] not in _STRUCTURAL_SLOTS]

    # A beat may legitimately hold more than one sentence ("5,000 labs
    # applied. 278 got it." is one idea), so the cap counts SENTENCES of
    # proof, not beats — otherwise a multi-sentence beat slips past it.
    def proof_sentences():
        return sum(len(split_sentences(kept[i]["sentence"])) for i in proof_indices())

    while proof_indices() and proof_sentences() > config.MAX_PROOF_BEATS:
        dropped.append(kept.pop(proof_indices()[-1]))
    while estimate_seconds(" ".join(b["sentence"] for b in kept)) > config.MAX_SECONDS:
        droppable = proof_indices()
        if not droppable:
            break  # structure alone exceeds the cap: report it, don't mangle it
        dropped.append(kept.pop(droppable[-1]))
    return kept, dropped


def build_draft(slots: dict, beats: list[dict]) -> dict:
    """Assemble the artifact from raw model beats. Pure — no IO, no model
    call — so bench.py and the tests can exercise it directly."""
    clean_beats = []
    seen_sentences: set[str] = set()
    for beat in beats:
        # The writer sometimes puts an audio tag in the clean field too.
        # script_text is the source of truth for subtitles and checking and
        # must never contain a bracketed span, so strip it here rather than
        # trusting the field to be clean.
        sentence = strip_tags(beat["sentence"])
        if not sentence:
            continue
        # The writer occasionally restates earlier beats verbatim at the end.
        # Repetition is never intended here — each beat is covered once.
        key = " ".join(prose.normalise(sentence))
        if key in seen_sentences:
            log.warning("dropping repeated beat: %r", sentence[:60])
            continue
        seen_sentences.add(key)
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
    lengths = [len(s.split()) for s in sentences]
    return {
        "script_text": script_text,
        "tts_text": tts_text,
        "beats": kept,
        "dropped_beats": dropped,
        "slots_used": sorted({b["slot"] for b in kept}),
        "sentences": sentences,
        "sentence_lengths": lengths,
        "word_count": len(script_text.split()),
        "estimated_seconds": estimate_seconds(script_text),
        "banned_constructions": banned,
    }


# --- quality gates ---------------------------------------------------------

class DraftRejected(RuntimeError):
    """A draft failed a deterministic gate. Carries what to tell the writer."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason} {detail}")
        self.reason = reason
        self.detail = detail


def _quote_problem(draft: dict, slots: dict) -> str | None:
    """The quote must survive into the script character for character, or
    checks/quotes.py will kill the run later. Catch it here — that is a bug in
    drafting, not something for check.py to discover."""
    quote = slots.get("quote")
    if not quote:
        return None
    used = any(b["slot"] == "quote" for b in draft["beats"])
    if not used:
        return None
    if prose.quote_matches(quote["text"], draft["script_text"]):
        return None
    return (f'the quote must appear character for character inside quotation '
            f'marks. Required exactly: "{quote["text"]}"')


def evaluate(draft: dict, slots: dict) -> None:
    """Raise DraftRejected if the draft is notes rather than copy."""
    sentences = draft["sentences"]

    passthrough = prose.find_passthrough(
        sentences, notes_text(slots),
        exempt=[(slots.get("quote") or {}).get("text", "")],
        proper_nouns=_proper_nouns(slots))
    if passthrough:
        detail = "\n".join(
            f'  - {p["run_words"]} words copied from the notes: "{p["shared"]}"\n'
            f'    in: {p["sentence"]}' for p in passthrough)
        raise DraftRejected(
            "These sentences are the notes read aloud, not copy written from "
            "the facts:", detail)

    problems = prose.rhythm_problems(
        sentences, exempt_long=[(slots.get("quote") or {}).get("text", "")])
    if problems:
        raise DraftRejected("The pacing is wrong:",
                            "\n".join(f"  - {p}" for p in problems))

    quote_problem = _quote_problem(draft, slots)
    if quote_problem:
        raise DraftRejected("The quote was altered:", f"  - {quote_problem}")


# --- generation ------------------------------------------------------------

def generate(report: dict, run, *, temperature: float = config.DRAFT_TEMPERATURE,
             label: str | None = None) -> dict:
    """Write the script, retrying once if a deterministic gate rejects it.

    Two failures in a row is a signal worth seeing — it means the prompt has
    stopped framing the task generatively — so it raises rather than
    returning something that failed."""
    slots = report["slots"]
    base_prompt = _PROMPT.format(
        max_proof=config.MAX_PROOF_BEATS,
        max_words=prose.MAX_SENTENCE_WORDS,
        short_words=prose.SHORT_SENTENCE_WORDS,
        max_run=prose.MAX_VERBATIM_RUN,
        tags=", ".join(config.AUDIO_TAGS),
        context=_context_for_prompt(slots, report["story"]),
        notes=_notes_for_prompt(slots))

    prompt = base_prompt
    last_error: DraftRejected | None = None
    for attempt in range(1, config.MAX_DRAFT_ATTEMPTS_PROSE + 1):
        suffix = f"-retry{attempt - 1}" if attempt > 1 else ""
        result = llm.json_call("draft", prompt, _DRAFT_SCHEMA, run=run,
                               system=_SYSTEM, temperature=temperature,
                               label=(label or "draft") + suffix)
        draft = build_draft(slots, result["beats"])
        try:
            evaluate(draft, slots)
        except DraftRejected as e:
            last_error = e
            log.warning("draft attempt %d rejected: %s", attempt, e.reason)
            prompt = _RETRY_PREAMBLE.format(reason=e.reason, detail=e.detail) \
                + base_prompt
            continue
        draft["story"] = report["story"]
        draft["attempts"] = attempt
        return draft

    raise DraftRejected(
        f"rejected {config.MAX_DRAFT_ATTEMPTS_PROSE} times — the prompt is not "
        f"framing this as writing. Last reason: {last_error.reason}",
        last_error.detail)


def run(run) -> None:
    report = run.read("report")
    draft = generate(report, run)
    run.write("draft", draft)
    log.info("%d beats, %d words, ~%.1fs, lengths %s, slots: %s",
             len(draft["beats"]), draft["word_count"], draft["estimated_seconds"],
             draft["sentence_lengths"], ", ".join(draft["slots_used"]))


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
    for beat in draft["beats"]:
        print(f"  [{beat['slot']:6}] ({len(beat['sentence'].split()):>2}w) {beat['sentence']}")
    print(f"\nscript: {draft['script_text']}")
    print(f"tts:    {draft['tts_text']}")
    print(f"\n{draft['word_count']} words, ~{draft['estimated_seconds']}s, "
          f"lengths {draft['sentence_lengths']}, "
          f"attempts {draft.get('attempts', 1)}, "
          f"slots used: {', '.join(draft['slots_used'])}")
