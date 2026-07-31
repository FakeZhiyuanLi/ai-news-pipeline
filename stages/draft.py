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
from checks import sentences

log = logging.getLogger(__name__)

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        # The on-screen overlay. Never spoken, never part of script_text.
        "caption": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # so_what is deliberately absent: it is context, never a beat.
                    "slot": {"type": "string",
                             "enum": ["lede", "number", "quote", "tension",
                                      "scale_anchor"]},
                    # What this beat DOES, as against which slot it came from.
                    # Recorded explicitly: a null kind leaves nothing
                    # downstream able to tell a funnel from a plain proof.
                    "kind": {"type": "string", "enum": list(config.BEAT_KINDS)},
                    "sentence": {"type": "string"},
                    "tts_sentence": {"type": "string"},
                },
                "required": ["slot", "kind", "sentence", "tts_sentence"],
            },
        },
    },
    "required": ["caption", "beats"],
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
  5,000 labs asked Washington for AI money. 278 got it. The pot was $5
  billion — and it skips universities entirely, going to individual
  researchers and private companies. Science, funded like a startup portfolio.
  AI policy professor Claes de Vreese: "You cannot use venture capital to
  force breakthroughs while virtually already selling the patent." The plan
  also cuts life sciences while boosting foundational biology, without saying
  where one ends and the other begins. 4,722 labs got nothing.

Study what changed. The opening went from 31 words to 7, and it does not
ANSWER — "5,000 labs asked" leaves you needing the next number, where
"Washington put $5 billion into AI research" is complete and inert. The funnel
runs large first, so the attrition lands. The "why it matters" never became
its own sentence — it became the framing, "funded like a startup portfolio".
The last line returns to the gap the opening opened, using a number the viewer
can now feel. Sentence lengths run 7, 3, 18, 6, 24, 20, 4. Nothing was
invented: every fact traces back to a note, including the closer's subtraction.

That closer works because THIS hook counted applicants, so the ones left out
are what the viewer is still holding. It is one instance of the move, not the
move itself — a hook about a price or a deadline bookends somewhere else
entirely. Lifting "N got nothing" onto a story that never counted a pool
produces a closer that bookends nothing.

That is register, not a template. Do not imitate its wording, its closer, or
its caption.
"""

_PROMPT = """\
Write the narration for this story.

STRUCTURE
  1. A HOOK, under 12 words. Not a lede. A lede ANSWERS; a hook OPENS A GAP
     that the rest of the script closes.

     THE TEST, and apply it literally: could this line serve as the CLOSING
     line of the script? If yes, it is a conclusion, not a hook — rewrite it.
       "Washington put $5 billion into AI research."  -> conclusion. Complete,
          inert, nothing follows from it.
       "5,000 labs asked Washington for AI money."    -> hook. The number
          demands the next one.

     Ways to open a gap, all of them grounded in the notes:
       - lead with the number whose meaning is not yet clear
       - lead with the LARGER half of a ratio
       - lead with the juxtaposition in the catch, when it is more surprising
         than any single number
     Never open with a question ("What if..."), never with a preamble, never
     with a company name as the grammatical subject.
  2. Proof beats, from the ranked numbers and the quote if there is one.
     At most {max_proof}, fewer is usually better, in the order given.
  3. The catch, from the tension note.
     If there is no tension note, there is no catch beat. Never invent one.
  4. A CLOSER, last — under {max_closer} words and SHORTER than every other
     sentence in the script.
     It BOOKENDS the hook: it returns to the gap the opening created, now that
     the viewer has the numbers.
     There is no house closer. The move is to reach back to whatever the HOOK
     opened, and the hook differs every time, so the closer must too:
       hook on a pool    -> land on who was left out
       hook on a price   -> land on what it buys, or does not
       hook on a claim   -> land on the part still unproven
       hook on a date    -> land on how little time is left
     It is grounded exactly like every other beat and goes through the same
     verification. It may restate the stake. It must NEVER introduce a fact
     that is not in the notes.
     BANNED in the closer: calls to action, "follow for more", sign-offs, "the
     future of X", "one thing is clear", "time will tell", and any sentence
     whose subject is "we" or "you".
     OMIT THE CLOSER ENTIRELY where the notes cannot support one that is not
     filler. A script that ends on a strong catch is better than one padded
     with a hollow closer. This is a real choice, not a formality.

BEAT KINDS — label every beat with what it DOES, not where it came from
  hook   the opening line. Exactly one, and it is first.
  proof  a single supporting fact.
  funnel a beat that pairs two numbers describing the same population.
  quote  the beat carrying the quotation.
  catch  the tension.
  closer the bookend. Exactly one if present, and it is last.

RHYTHM — this is checked, and a script that fails is sent back
  - No sentence over {max_words} words. The sentence carrying the quotation is
    exempt: reproduce the quote in full, NEVER trim it to fit the ceiling.
    Trimming it fails a different check and cannot be recovered by trimming
    further — if the quote is long, the sentence is long, and that is correct.
    Compress the ATTRIBUTION instead, never the quoted words.
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
    Label that beat kind=funnel.
  - FUNNEL ORDER: whenever two numbers share a unit and one counts a SUBSET of
    the other, the LARGER one comes first. "5,000 applied. 278 got it."
    lands; "278 selected. Over 5,000 applied." throws the attrition away by
    revealing the outcome before the pool it came from.

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

THE CAPTION — on-screen text, never spoken
  A bold overlay that sits near the top of the frame for the whole video. It
  is the single most valuable piece of text you write, because it is legible
  and screenshottable.
  - {caption_min} to {caption_max} words. It must read at a glance on a phone.
  - It is THE HOOK AS TEXT — the same gap, written for the eye. It is NOT a
    copy of the first spoken sentence: the subtitles already show that, and
    repeating it wastes the strongest real estate in the frame. Sharing
    {caption_run} or more consecutive words with the first sentence is
    rejected.
  - IT IS CLAIM-BEARING and is verified exactly like a spoken sentence. An
    unverified headline sitting on screen for the full duration is worse than
    an unverified spoken line. Ground it in the notes.
  - Sentence case. No terminal period, no emoji, no hashtags.
  - It never appears in any sentence or tts_sentence.
  - Write it from the HOOK, not from whichever phrase in the notes sounds most
    quotable. Two stories that share a stock phrase get the same caption that
    way, and the caption stops describing the story it is on.

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

# "Start over" was measured to be actively harmful once there were five gates:
# 60% of first drafts are clean, but a retry that discards a draft which was
# right about everything except its closer re-rolls all five gates instead of
# one, so three attempts kept failing on three DIFFERENT gates. Show the writer
# what it produced, name the one defect, and keep the rest.
_RETRY_PREAMBLE = """\
Your previous attempt was rejected for ONE reason. Everything else about it
was accepted.

WHAT YOU WROTE
{previous}

WHAT IS WRONG — {reason}
{detail}

Fix exactly that. Keep every sentence that is not implicated, word for word:
the rest of the script passed its checks and rewriting it risks breaking
something that already worked. Where a sentence must change, rewrite that
sentence from the underlying fact rather than swapping synonyms into it.

"""


# --- sentence splitting ----------------------------------------------------

# Shared with claims.py: the checker must see exactly the sentences the writer
# produced, or a claim can be judged against text that was never a sentence.
split_sentences = sentences.split


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
    """Names from the notes: unavoidable in any retelling, so a run made only
    of these (plus figures and stopwords) does not count as passthrough.

    Hyphenated and alphanumeric identifiers matter as much as capitalised
    words. `ARC-AGI-3` normalises to three tokens — `arc`, `agi`, `3` — and a
    pattern of `[A-Z][a-zA-Z]+` misses the digit, so "38.3% on the ARC-AGI-3
    public set" was flagged as 8 words of passthrough when it has no
    paraphrase at all. That made the story undraftable: three redrafts, all
    rejected for the one phrase the script could not avoid.
    """
    names = set()
    for chunk in [notes_text(slots), (slots.get("quote") or {}).get("name", "")]:
        text = chunk or ""
        names.update(re.findall(r"\b[A-Z][a-zA-Z]+\b", text))
        # Product and benchmark names: ARC-AGI-3, GPT-5.6, K2.6, Llama-3.
        for token in re.findall(r"\b[A-Za-z]+(?:[-.][A-Za-z0-9]+)+\b", text):
            names.update(re.findall(r"[a-z0-9]+", token.lower()))
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


# Never dropped: the way in, the catch, the quote, and the bookend. A named
# human saying something in their own words is the most valuable proof a
# script has, and the cap must not spend it on a fourth number. The closer is
# structural for the same reason the hook is — dropping it leaves the script
# stopping mid-thought, which is the shape this exists to fix.
_STRUCTURAL_SLOTS = ("lede", "tension", "quote")
_STRUCTURAL_KINDS = ("hook", "catch", "quote", "closer")


def _is_structural(beat: dict) -> bool:
    return (beat["slot"] in _STRUCTURAL_SLOTS
            or beat.get("kind") in _STRUCTURAL_KINDS)


def _truncate_to_budget(beats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop from the bottom of the ranked proof beats — never the hook, the
    catch, the quote, or the closer — until the script fits MAX_PROOF_BEATS
    and MAX_SECONDS."""
    kept, dropped = list(beats), []

    def proof_indices():
        return [i for i, b in enumerate(kept) if not _is_structural(b)]

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


# --- beat kinds ------------------------------------------------------------

# Where the writer omits a kind, the slot implies one. This is a fallback, not
# the mechanism: `funnel` and `closer` cannot be inferred from a slot at all —
# both are `number` — which is exactly why the writer is asked for the kind
# and why a missing one is worth a warning.
_KIND_FROM_SLOT = {
    "lede": "hook",
    "tension": "catch",
    "quote": "quote",
    "number": "proof",
    "scale_anchor": "proof",
}


def _beat_kind(beat: dict, *, index: int) -> str:
    kind = (beat.get("kind") or "").strip().lower()
    if kind in config.BEAT_KINDS:
        return kind
    inferred = _KIND_FROM_SLOT.get(beat["slot"], "proof")
    if index == 0:
        inferred = "hook"
    log.warning("beat %d has no usable kind (%r); inferring %r from slot %r",
                index, beat.get("kind"), inferred, beat["slot"])
    return inferred


def kind_problems(beats: list[dict]) -> list[str]:
    """The structural invariants: every beat labelled, the first is the hook,
    the last is the catch or the closer. Asserted before script.json is
    written because `beats: [None, None, None]` is what this replaces."""
    if not beats:
        return ["no beats"]
    problems = [f"beat {i} has no kind" for i, b in enumerate(beats)
                if b.get("kind") not in config.BEAT_KINDS]
    if problems:
        return problems
    kinds = [b["kind"] for b in beats]
    # A hook with nothing after it is a collapsed draft, not a misordered one.
    # Reporting it as "the last beat is 'hook'" sent the writer looking for an
    # ordering bug that was not there.
    if len(beats) == 1:
        return [f"the script is a single {kinds[0]!r} beat — a hook with "
                "nothing after it is not a script"]
    if kinds[0] != "hook":
        problems.append(f"the first beat is {kinds[0]!r}, not 'hook'")
    if kinds[-1] not in ("closer", "catch"):
        problems.append(
            f"the last beat is {kinds[-1]!r} — a script ends on the catch or "
            "the closer, never mid-proof")
    for extra in ("hook", "closer"):
        if kinds.count(extra) > 1:
            problems.append(f"{kinds.count(extra)} beats labelled {extra!r}; "
                            "there is at most one")
    return problems


# --- the closer ------------------------------------------------------------

# Filler that reads like a closer without being one. These are the shapes a
# model reaches for when the notes cannot support a real bookend — at which
# point the correct move is to OMIT the closer, not to write one of these.
_CLOSER_FILLER = (
    (r"\bthe future of\b", '"the future of X"'),
    (r"\bone thing is clear\b", '"one thing is clear"'),
    (r"\b(?:only )?time will tell\b", '"time will tell"'),
    (r"\bremains to be seen\b", '"remains to be seen"'),
    (r"\bwatch this space\b", '"watch this space"'),
    (r"\bstay tuned\b", '"stay tuned"'),
    (r"^\s*(?:but )?(?:we|you)\b", 'a closer whose subject is "we" or "you"'),
)


def closer_problems(beats: list[dict]) -> list[str]:
    """The closer is optional; when present it must be the shortest line and
    must not be filler. Omitting it is a correct result, so absence is never
    a problem here."""
    closers = [b for b in beats if b.get("kind") == "closer"]
    if not closers:
        return []
    closer = closers[-1]
    sentence = closer["sentence"]
    problems = []
    if beats[-1] is not closer:
        problems.append("the closer is not the last beat")
    words = len(sentence.split())
    if words > config.MAX_CLOSER_WORDS:
        problems.append(f'the closer is {words} words ("{sentence}") — it must '
                        f"be under {config.MAX_CLOSER_WORDS + 1}")
    # "Shorter than every other sentence" as stated is unsatisfiable when a
    # funnel beat is already 3 words — it demands a 2-word closer, which is
    # worse writing, not better. Ties with the shortest line are allowed; the
    # requirement that matters is that nothing is shorter than the landing.
    others = [len(b["sentence"].split()) for b in beats if b is not closer]
    if others and words > min(others):
        problems.append(
            f"the closer is {words} words, longer than the shortest other "
            f"sentence ({min(others)}) — the bookend lands because nothing in "
            "the script is shorter than it")
    for pattern, label in _CLOSER_FILLER:
        if re.search(pattern, sentence, re.IGNORECASE):
            problems.append(
                f'the closer is filler — {label}. Omit the closer entirely '
                "rather than padding: ending on the catch is a correct result")
    return problems


# --- caption ---------------------------------------------------------------

# Function words a caption must not end on. A caption is trimmed to the word
# ceiling when a repair overruns, and "3x the score but it's not the" reads as
# a truncation rather than a headline.
_DANGLING = frozenset("""
a an the of on in at to for from with by and or but its it is was were be
that this these those as into over under after before than then so not
""".split())


def clean_caption(raw: str) -> str:
    """Sentence case, no terminal period, no emoji, no hashtags — enforced
    here rather than hoped for, because this text is on screen for the whole
    video and is the part viewers screenshot."""
    caption = strip_tags(raw or "").strip().strip('"“”')
    caption = re.sub(r"#\w+", " ", caption)
    # Emoji and other pictographs, by codepoint range.
    caption = re.sub(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF️]", " ",
                     caption)
    caption = re.sub(r"\s+", " ", caption).strip()
    caption = caption.rstrip(".").strip()
    # A caption trimmed to the word ceiling can end mid-phrase ("...but it's
    # not the"). Drop trailing function words and punctuation so the result
    # still reads as a finished line rather than a truncation.
    while True:
        stripped = caption.rstrip(" ,;:—-").strip()
        words = stripped.split()
        if len(words) > 1 and words[-1].lower() in _DANGLING:
            caption = " ".join(words[:-1])
            continue
        return stripped


def caption_problems(caption: str, first_sentence: str) -> list[str]:
    """Everything about the caption that a model cannot be trusted to get
    right. Returns human-readable problems, empty if it is fine."""
    problems = []
    if not caption:
        return ["the caption is empty"]
    words = caption.split()
    if not config.CAPTION_MIN_WORDS <= len(words) <= config.CAPTION_MAX_WORDS:
        problem = (f'the caption is {len(words)} words ("{caption}") — it must '
                   f"be {config.CAPTION_MIN_WORDS}-{config.CAPTION_MAX_WORDS} "
                   "to read at a glance on a phone")
        # Naming the count alone did not land: retries came back 10 and 11
        # words against a 9-word cap. Show the cut, because the overrun is
        # almost always a trailing qualifier the caption does not need.
        if len(words) > config.CAPTION_MAX_WORDS:
            problem += (". Drop the trailing qualifier — "
                        f'"{" ".join(words[:config.CAPTION_MAX_WORDS])}" is '
                        "already the whole point")
        problems.append(problem)
    # The caption must be the hook as TEXT, not the spoken line repeated: the
    # subtitles already show that sentence, so duplicating it spends the
    # strongest real estate in the frame on nothing.
    run, shared = prose.longest_shared_run(caption, first_sentence)
    if run >= config.CAPTION_MAX_SHARED_RUN:
        problems.append(
            f'the caption repeats the first sentence ({run} consecutive words: '
            f'"{shared}") — write the hook as text, not the spoken line again')
    return problems


def build_draft(slots: dict, beats: list[dict], caption: str = "") -> dict:
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
            "kind": _beat_kind(beat, index=len(clean_beats)),
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

    caption_text = clean_caption(caption)
    # The caption is displayed, never spoken. If it reached either text field
    # it would be narrated aloud and subtitled — so this is an invariant, the
    # same way tts_text stripping to script_text is.
    if caption_text:
        spoken = " ".join(prose.normalise(script_text))
        if " ".join(prose.normalise(caption_text)) in spoken:
            raise RuntimeError(
                f"the caption appears in the spoken script: {caption_text!r}")

    sentences = split_sentences(script_text)
    banned = find_banned(script_text)
    if banned:
        log.warning("banned constructions present: %s", "; ".join(banned))
    lengths = [len(s.split()) for s in sentences]
    return {
        "caption": caption_text,
        "script_text": script_text,
        "tts_text": tts_text,
        "beats": kept,
        "beat_kinds": [b["kind"] for b in kept],
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


# Distinctive spans from the worked example in _SYSTEM. Under the combined
# load of a forbidden-claims preamble, the previous draft and a named defect,
# the writer once returned "5,000 labs asked Washington for AI money" as the
# only beat of a script about post-quantum cryptography — the example is a
# complete, correct-looking script, so it is what a struggling model reaches
# for. Unlike every other gate this one guards against narrating a fact from
# an unrelated story, so it is checked on the words, not left to the prompt.
# Distinctive spans from the worked example in _SYSTEM.
#
# Keyword anchoring was tried and abandoned: a legitimate draft of the example
# story says "5,000 labs asked Washington" while its notes say "the Trump
# administration ... over 5,000 grant applications", so neither the phrase nor
# any one keyword is reliably present in the honest case. What separates the
# two cases is whether the script's FACTS trace to the notes — leaked example
# text carries the example's numbers, which a different story cannot have.
_EXAMPLE_SPANS = (
    "5,000 labs asked washington",
    "genesis mission",
    "claes de vreese",
    "funded like a startup portfolio",
    "you cannot use venture capital",
    "4,722",
)

_NUMBER_RE = re.compile(r"\d[\d,.]*")


def example_leakage(script_text: str, slots: dict) -> list[str]:
    """Spans lifted from the prompt's worked example rather than the notes.

    The example story could genuinely come round again, so a span is leakage
    only when the sentence carrying it also carries a number the notes do not
    have. That is the difference between writing about the example story and
    reciting the example.
    """
    notes_blob = (notes_text(slots) + " "
                  + (slots.get("quote") or {}).get("text", "") + " "
                  + (slots.get("quote") or {}).get("name", ""))
    notes_words = " ".join(prose.normalise(notes_blob))
    notes_numbers = {n.replace(",", "").rstrip(".")
                     for n in _NUMBER_RE.findall(notes_blob)}

    found = []
    for sentence in split_sentences(script_text):
        words = " ".join(prose.normalise(sentence))
        for span in _EXAMPLE_SPANS:
            needle = " ".join(prose.normalise(span))
            if not needle or needle not in words:
                continue
            if needle in notes_words:
                continue  # the notes say it too: this really is that story
            alien = {n.replace(",", "").rstrip(".")
                     for n in _NUMBER_RE.findall(sentence)} - notes_numbers
            if alien or not _NUMBER_RE.search(sentence):
                found.append(span)
    return found


def evaluate(draft: dict, slots: dict) -> None:
    """Raise DraftRejected if the draft is notes rather than copy."""
    sentences = draft["sentences"]

    # Checked first: this is the only gate whose failure means the script is
    # about a DIFFERENT STORY, which no amount of rewriting downstream fixes.
    leaked = example_leakage(draft["script_text"], slots)
    if leaked:
        raise DraftRejected(
            "This is the worked example from the instructions, not this story:",
            "\n".join(f'  - "{span}" belongs to the example about AI research '
                      "funding and has nothing to do with the story you were "
                      "given" for span in leaked))

    # The scale anchor is GIVEN text, not written — report.py phrases it as a
    # whole sentence ("The compute cost of $100,000 is about 1.5 times the
    # average US annual salary"), so any faithful use of it is 8+ verbatim
    # words and the gate fired on the only correct way to say it. Exempt like
    # the quote: the sentence is still judged on the words around it.
    anchor = slots.get("scale_anchor") or {}
    passthrough = prose.find_passthrough(
        sentences, notes_text(slots),
        exempt=[(slots.get("quote") or {}).get("text", ""),
                anchor.get("statement", "")],
        proper_nouns=_proper_nouns(slots))
    if passthrough:
        detail = "\n".join(
            f'  - {p["run_words"]} words copied from the notes: "{p["shared"]}"\n'
            f'    in: {p["sentence"]}' for p in passthrough)
        raise DraftRejected(
            "These sentences are the notes read aloud, not copy written from "
            "the facts:", detail)

    # The quote check runs BEFORE the pacing check. A trimmed quote loses its
    # length exemption, so it surfaces as "sentence over 20 words" — and the
    # writer, told the pacing is wrong, trims the quote further and fails
    # again. Reporting the real cause first is the difference between a retry
    # that can succeed and one that cannot.
    quote_problem = _quote_problem(draft, slots)
    if quote_problem:
        raise DraftRejected("The quote was altered:", f"  - {quote_problem}")

    problems = prose.rhythm_problems(
        sentences, exempt_long=[(slots.get("quote") or {}).get("text", "")])
    if problems:
        raise DraftRejected("The pacing is wrong:",
                            "\n".join(f"  - {p}" for p in problems))

    problems = caption_problems(draft["caption"], sentences[0] if sentences else "")
    if problems:
        raise DraftRejected("The on-screen caption is wrong:",
                            "\n".join(f"  - {p}" for p in problems))

    problems = closer_problems(draft["beats"])
    if problems:
        raise DraftRejected("The closer is wrong:",
                            "\n".join(f"  - {p}" for p in problems))

    problems = kind_problems(draft["beats"])
    if problems:
        raise DraftRejected("The beat structure is wrong:",
                            "\n".join(f"  - {p}" for p in problems))


# --- generation ------------------------------------------------------------

_CAPTION_SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
}

_CAPTION_REPAIR = """\
Write the on-screen caption for this script. Nothing else — the script is
already written and is not being changed.

THE SCRIPT (already final, and already shown as subtitles)
{script}

THE CAPTION
  - {caption_min} to {caption_max} words. COUNT THEM. Every rejected attempt
    so far has been over by one or two, always because a qualifier was tacked
    on the end — "...but only on the public test", "...that scored 7.8%". Cut
    that clause before you count, not after. The caption states ONE thing.
  - It is the HOOK AS TEXT: the same gap the first line opens, written for the
    eye. It is NOT a copy of the first sentence — that is already on screen as
    a subtitle, so repeating it wastes the best real estate in the frame.
    Sharing {caption_run} or more consecutive words with the first sentence is
    rejected.
  - Grounded in the script. It is verified like a spoken line.
  - Sentence case, no terminal period, no emoji, no hashtags.

The previous attempt was rejected:
{problems}
"""


def _repair_caption(draft: dict, slots: dict, run, *, label: str) -> dict:
    """Rewrite only the caption when only the caption is wrong.

    One cheap, tightly-scoped call against a script that already passed the
    prose gates, rather than throwing away good writing over one field.
    """
    sentences = draft["sentences"]
    problems = caption_problems(draft["caption"], sentences[0] if sentences else "")
    if not problems:
        return draft
    log.info("repairing caption in place: %s", "; ".join(problems))
    try:
        result = llm.json_call(
            "draft",
            _CAPTION_REPAIR.format(
                script=draft["script_text"],
                caption_min=config.CAPTION_MIN_WORDS,
                caption_max=config.CAPTION_MAX_WORDS,
                caption_run=config.CAPTION_MAX_SHARED_RUN,
                problems="\n".join(f"  - {p}" for p in problems)),
            _CAPTION_SCHEMA, run=run, temperature=config.DRAFT_TEMPERATURE,
            label=f"{label}-caption")
    except Exception as e:  # noqa: BLE001 - the outer gate reports the failure
        log.warning("caption repair call failed (%s); keeping the original", e)
        return draft
    repaired = clean_caption(result.get("caption", ""))
    first = sentences[0] if sentences else ""
    # The repair overruns too, always by a word or two ("One toggle, 3x the
    # score—but it's not the real test" came back at 10 against a cap of 9).
    # Spending the whole draft on that is the worst trade available, and the
    # overrun is ALWAYS a trailing qualifier the repair prompt already names
    # as droppable — so drop it here rather than discarding good writing.
    # NOT trimmed to fit. Cutting at the word ceiling was tried and reverted:
    # it produced "How OpenAI got 38% from a model that scored" — nine words,
    # passing every check, and a visible truncation on screen for the whole
    # video. Cutting at a clause boundary instead fails whenever the first
    # nine words contain no boundary, which is the common case. A caption is
    # written short or it is not used; the draft is rejected and retried, and
    # the retry keeps the prose it already got right.
    if caption_problems(repaired, first):
        return draft  # still wrong: let evaluate() reject and retry the draft
    # The caption is displayed, never spoken — the same invariant build_draft
    # asserts. A repair must not be the route around it.
    if " ".join(prose.normalise(repaired)) in " ".join(prose.normalise(draft["script_text"])):
        log.warning("repaired caption appears in the spoken script; keeping original")
        return draft
    return {**draft, "caption": repaired}


_FORBIDDEN_PREAMBLE = """\
CHECKING REJECTED THESE CLAIMS. The article does not support them. Do not
write them, and do not write anything that implies them:

{forbidden}

Write the story from what the notes DO support. If dropping one costs you a
beat, the script is simply shorter — that is a correct result.

"""


def generate(report: dict, run, *, temperature: float = config.DRAFT_TEMPERATURE,
             label: str | None = None,
             forbidden: list[str] | None = None) -> dict:
    """Write the script, retrying once if a deterministic gate rejects it.

    Two failures in a row is a signal worth seeing — it means the prompt has
    stopped framing the task generatively — so it raises rather than
    returning something that failed."""
    slots = report["slots"]
    base_prompt = _PROMPT.format(
        max_proof=config.MAX_PROOF_BEATS,
        max_closer=config.MAX_CLOSER_WORDS,
        caption_min=config.CAPTION_MIN_WORDS,
        caption_max=config.CAPTION_MAX_WORDS,
        caption_run=config.CAPTION_MAX_SHARED_RUN,
        max_words=prose.MAX_SENTENCE_WORDS,
        short_words=prose.SHORT_SENTENCE_WORDS,
        max_run=prose.MAX_VERBATIM_RUN,
        tags=", ".join(config.AUDIO_TAGS),
        context=_context_for_prompt(slots, report["story"]),
        notes=_notes_for_prompt(slots))
    if forbidden:
        base_prompt = _FORBIDDEN_PREAMBLE.format(
            forbidden="\n".join(f"  - {c}" for c in forbidden)) + base_prompt

    prompt = base_prompt
    last_error: DraftRejected | None = None
    for attempt in range(1, config.MAX_DRAFT_ATTEMPTS_PROSE + 1):
        suffix = f"-retry{attempt - 1}" if attempt > 1 else ""
        result = llm.json_call("draft", prompt, _DRAFT_SCHEMA, run=run,
                               system=_SYSTEM, temperature=temperature,
                               label=(label or "draft") + suffix)
        draft = build_draft(slots, result["beats"], result.get("caption", ""))
        # Five independent gates each failing ~17% of the time makes a clean
        # draft improbable, and stories the bench writes well were being lost
        # to a different gate on every attempt. The caption is ONE FIELD and
        # independent of the prose, so a bad one is repaired in place against
        # the already-good script rather than costing the whole draft. The
        # prose gates below still reject — they describe the writing itself,
        # which cannot be patched from outside.
        draft = _repair_caption(draft, slots, run,
                               label=(label or "draft") + suffix)
        try:
            evaluate(draft, slots)
        except DraftRejected as e:
            last_error = e
            log.warning("draft attempt %d rejected: %s", attempt, e.reason)
            previous = "\n".join(
                f"  [{b['kind']}] {b['sentence']}" for b in draft["beats"])
            if draft.get("caption"):
                previous += f"\n  caption: {draft['caption']}"
            prompt = _RETRY_PREAMBLE.format(
                previous=previous, reason=e.reason, detail=e.detail) + base_prompt
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

    runs.load_env()
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
