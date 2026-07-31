"""Claim extraction, classification and routing.

VERIFY CLAIMS, NOT SENTENCES. Marking a whole sentence SUPPORTED or
UNSUPPORTED conflates "asserts something false" with "isn't in the bullet
list". Framing and judgment are never entailed by a fact list, so
per-sentence verification makes it structurally impossible for a script to
have a voice.

A PRONOUN SUBJECT IS CLAIM-BEARING UNTIL RESOLVED. "That is a research demo"
reads as framing but asserts a fact about the preceding sentence — and in the
previous build it directly contradicted it, since that sentence said "now in
full production". The preceding sentence is passed as context and the
referent resolved before classifying. Judgment must SURVIVE resolution:
"This is the part that should worry NVIDIA" resolves to a claim about
significance and passes.

Routing, cheapest first:
  quote      -> checks.quotes, a substring search. Never a model.
  arithmetic -> checks.numbers, recomputed in Python.
  factual    -> entailment against the report slots (one model call, batched).
  judgment,
  framing    -> pass, unless anaphoric and unresolved.
"""

import logging

import config
import llm
from checks import numbers, quotes, sentences

log = logging.getLogger(__name__)

FACTUAL = "factual"
QUOTE = "quote"
ARITHMETIC = "arithmetic"
JUDGMENT = "judgment"
FRAMING = "framing"

SUPPORTED = "SUPPORTED"
REFUTED = "REFUTED"
UNVERIFIED = "UNVERIFIED"

# Kept tight on purpose: GLM has the highest output price in this set and
# claim classification is the most verbose output in the pipeline. Verdict
# plus a short reason, never a restated sentence.
_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "s": {"type": "integer", "description": "sentence index"},
                    "claim": {"type": "string",
                              "description": "the assertion, pronouns resolved"},
                    "type": {"type": "string",
                             "enum": [FACTUAL, QUOTE, ARITHMETIC, JUDGMENT, FRAMING]},
                    "anaphoric": {"type": "boolean"},
                },
                "required": ["s", "claim", "type", "anaphoric"],
            },
        },
    },
    "required": ["claims"],
}

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer", "description": "claim index"},
                    "v": {"type": "string", "enum": [SUPPORTED, REFUTED]},
                    "why": {"type": "string", "description": "at most 12 words"},
                },
                "required": ["i", "v", "why"],
            },
        },
    },
    "required": ["verdicts"],
}

_EXTRACT_PROMPT = """\
Extract the individual CLAIMS from this video script and classify each one.

A sentence may carry more than one claim, or none. Judge claims, not
sentences.

TYPES
  factual    — asserts something about the world that could be checked
  quote      — words attributed to a named person as their own
  arithmetic — a computed comparison ("about 27 mid-range GPUs")
  judgment   — an evaluation the writer is making (significance, surprise,
               who should be worried). NOT a claim about the world.
  framing    — narrative connective tissue that asserts nothing checkable

PRONOUNS
Any sentence whose subject is a pronoun — it, this, that, they — is
claim-bearing until you resolve the referent. Use the preceding sentence,
given below, and write the claim with the pronoun REPLACED by what it refers
to. Set anaphoric=true for these.

Resolution decides the type, and this is the most important call you make.
Classify what the claim asserts AFTER the pronoun is replaced:

  "That is a research demo", after "the system is in full production",
  resolves to "the system is a research demo" — a FACTUAL claim about the
  world, and one that contradicts the preceding sentence. Type it factual.

  "This is the part that should worry NVIDIA", after a fact about a
  competitor's chip, resolves to "the preceding fact is significant for
  NVIDIA" — an evaluation of significance, not a statement about what
  happened. Type it JUDGMENT. Judgment survives resolution, and a judgment
  is not made false by the article failing to state it.

The test: after resolution, could the article prove the claim wrong? If yes
it is factual. If it could only disagree with your reading, it is judgment.

Write each claim as a short standalone assertion. Do not restate the sentence.

SCRIPT
{numbered}
"""

_VERIFY_PROMPT = """\
Does this article support each of the following claims?

Answer per claim:
  SUPPORTED — the article states this, or states something that entails it
  REFUTED   — the article contradicts it, or the article simply does not
              contain it. Absence is not support.

Be strict about specifics. A claim adding a detail the article does not carry
— a price, a tier, a guarantee like "without extra fees" — is REFUTED even if
everything around it is right.

Keep each reason to a few words.

CLAIMS
{claims}

ARTICLE
{source}
"""


def _numbered(script_sentences: list[str], caption: str = "") -> str:
    lines = []
    for i, sentence in enumerate(script_sentences):
        previous = script_sentences[i - 1] if i else "(none — first sentence)"
        lines.append(f"[{i}] {sentence}\n    preceding: {previous}")
    if caption:
        # Appended last so the spoken sentences keep their indices, and marked
        # so the checker knows it has no preceding sentence to resolve against
        # — an on-screen caption stands alone by construction.
        lines.append(f"[{len(script_sentences)}] {caption}\n"
                     "    preceding: (none — this is the ON-SCREEN CAPTION, "
                     "displayed for the whole video, not spoken. It stands "
                     "alone: resolve nothing against the script.)")
    return "\n".join(lines)


def extract(script_text: str, run, *, label: str | None = None,
            caption: str = "") -> list[dict]:
    """One "check" call over the whole script at temperature 0.

    The caption rides along as a final indexed unit. It is claim-bearing —
    legible and screenshottable for the full duration — so it is verified on
    exactly the same path as a spoken sentence, at no extra call.
    """
    script_sentences = sentences.split(script_text)
    if not script_sentences and not caption:
        return []
    units = script_sentences + ([caption] if caption else [])
    result = llm.json_call("check", _EXTRACT_PROMPT.format(
        numbered=_numbered(script_sentences, caption)), _EXTRACT_SCHEMA,
        run=run, temperature=0.0, label=label or "check-extract")
    claims = []
    caption_index = len(script_sentences) if caption else None
    for raw in result["claims"]:
        index = raw["s"]
        claims.append({
            "sentence_index": index,
            "sentence": units[index] if 0 <= index < len(units) else "",
            "is_caption": index == caption_index,
            "claim": raw["claim"],
            "type": raw["type"],
            "anaphoric": raw["anaphoric"],
        })
    return claims


# --- routing ---------------------------------------------------------------

def route(claims: list[dict], report_slots: dict) -> tuple[list[dict], list[dict]]:
    """Settle everything Python can settle. Returns (settled, needs_model).

    Deterministic checks run FIRST and are the only ones immune to shared
    model priors — which matters even with three model families in play.
    """
    settled, pending = [], []
    quote_slot = report_slots.get("quote") or {}
    anchor_slot = report_slots.get("scale_anchor") or {}

    for claim in claims:
        kind = claim["type"]
        if kind == QUOTE:
            source = quote_slot.get("text", "")
            ok = bool(source) and quotes.verify(claim["claim"], source)
            if not ok and source:
                # The claim text may wrap the quote in attribution; check the
                # other direction too.
                ok = quotes.verify(source, claim["claim"])
            settled.append({**claim,
                            "verdict": SUPPORTED if ok else REFUTED,
                            "checked_by": "checks.quotes",
                            "why": "verbatim in the report quote" if ok
                                   else (quotes.find_altered(claim["claim"], source)
                                         or "no quote in the report to match")})
        elif kind == ARITHMETIC:
            expression = anchor_slot.get("anchor_math", "")
            if not expression:
                # No scale anchor in the report means there is no anchor
                # arithmetic to recompute — NOT that the claim is false. The
                # writer can do correct arithmetic over two report numbers
                # ("$25 out against $15 is $10 more"), and auto-refuting it
                # here reported "'' does not evaluate: invalid syntax" as
                # though the SCRIPT were malformed, then forced a redraft.
                # Hand it to the checker like any other factual claim.
                pending.append({**claim, "type": FACTUAL,
                                "reclassified_from": ARITHMETIC})
                continue
            claimed = _number_in(claim["claim"])
            ok, _computed, why = numbers.verify_detail(expression, claimed)
            if ok and not numbers.uses_known_reference(expression):
                ok, why = False, ("divides by a value that is not in "
                                  "REFERENCE_SCALES")
            settled.append({**claim,
                            "verdict": SUPPORTED if ok else REFUTED,
                            "checked_by": "checks.numbers",
                            "why": why})
        elif kind in (JUDGMENT, FRAMING):
            # Framing and judgment are never entailed by a fact list, so they
            # pass without a model call — that is what lets a script have a
            # voice at all.
            #
            # Anaphora is handled at CLASSIFICATION, not here: the extractor
            # resolves the pronoun and then types the resolved claim. If it
            # still reads as judgment after resolution, it IS judgment —
            # "This is the part that should worry NVIDIA" resolves to a claim
            # about significance and passes. Sending a resolved judgment to
            # entailment gets it refuted for not appearing in the article,
            # which is the per-sentence verification failure this design
            # exists to avoid.
            settled.append({**claim, "verdict": SUPPORTED,
                            "checked_by": "type",
                            "why": f"{kind} asserts nothing about the world"
                                   + (" (pronoun resolved before classifying)"
                                      if claim["anaphoric"] else "")})
        else:
            pending.append(claim)
    return settled, pending


def _slots_as_text(slots: dict) -> str:
    lines = []
    for key in ("lede", "so_what", "tension"):
        if slots.get(key):
            lines.append(f"- {slots[key]}")
    for number in slots.get("numbers") or []:
        lines.append(f"- {number}")
    anchor = slots.get("scale_anchor")
    if anchor:
        lines.append(f"- {anchor.get('statement', '')}")
    quote = slots.get("quote")
    if quote:
        lines.append(f'- {quote["name"]} said: "{quote["text"]}"')
    return "\n".join(lines)


def _number_in(text: str) -> float | None:
    import re
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)", text.replace(",", ""))
    return float(match.group(1)) if match else None


def verify_against_source(pending: list[dict], source_text: str, run, *,
                          report_slots: dict | None = None,
                          label: str | None = None) -> list[dict]:
    """ONE batched re-extraction for every claim Python could not settle.

    The report slots are prepended to the source: a claim drawn from the
    notes the writer was given must not be refuted because the article
    phrases it differently, and re-verifying material the extractor already
    took from this article is wasted output on the priciest model in the set.
    """
    if not pending:
        return []
    listed = "\n".join(f"[{i}] {c['claim']}" for i, c in enumerate(pending))
    material = source_text
    if report_slots:
        material = ("ALREADY VERIFIED FROM THIS ARTICLE — treat as part of it:\n"
                    + _slots_as_text(report_slots)
                    + "\n\nFULL ARTICLE TEXT:\n" + source_text)
    result = llm.json_call("check", _VERIFY_PROMPT.format(
        claims=listed, source=material), _VERIFY_SCHEMA,
        run=run, temperature=0.0, label=label or "check-verify")
    by_index = {v["i"]: v for v in result["verdicts"]}
    out = []
    for i, claim in enumerate(pending):
        verdict = by_index.get(i)
        if verdict is None:
            out.append({**claim, "verdict": UNVERIFIED, "checked_by": "check model",
                        "why": "the checker returned no verdict for this claim"})
        else:
            out.append({**claim, "verdict": verdict["v"],
                        "checked_by": "check model", "why": verdict["why"]})
    return out


def counts_by_type(claims: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim["type"]] = counts.get(claim["type"], 0) + 1
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    slots = {
        "quote": {"text": "You cannot use venture capital to force breakthroughs",
                  "name": "Claes de Vreese", "role": "AI policy professor"},
        "scale_anchor": {"anchor_math": "15000 / 550", "statement": "about 27 GPUs"},
    }
    fixture = [
        {"sentence_index": 0, "sentence": "x", "type": QUOTE, "anaphoric": False,
         "claim": "You cannot use venture capital to force breakthroughs"},
        {"sentence_index": 1, "sentence": "x", "type": QUOTE, "anaphoric": False,
         "claim": "You cannot use venture capital to force miracles"},
        {"sentence_index": 2, "sentence": "x", "type": ARITHMETIC, "anaphoric": False,
         "claim": "that is about 27 mid-range GPUs"},
        {"sentence_index": 3, "sentence": "x", "type": ARITHMETIC, "anaphoric": False,
         "claim": "that is about 82 mid-range GPUs"},
        {"sentence_index": 4, "sentence": "x", "type": FRAMING, "anaphoric": False,
         "claim": "Science, funded like a startup portfolio"},
        {"sentence_index": 5, "sentence": "x", "type": JUDGMENT, "anaphoric": False,
         "claim": "this should worry NVIDIA"},
        {"sentence_index": 6, "sentence": "x", "type": FACTUAL, "anaphoric": False,
         "claim": "it spans free, Go, Plus and Pro tiers without extra fees"},
    ]
    settled, pending = route(fixture, slots)
    for claim in settled:
        print(f"{claim['verdict']:10} {claim['type']:10} {claim['claim'][:44]:46} "
              f"({claim['checked_by']}) {claim['why'][:44]}")
    for claim in pending:
        print(f"{'PENDING':10} {claim['type']:10} {claim['claim'][:44]:46} "
              "-> needs the source text")

    verdicts = {c["claim"]: c["verdict"] for c in settled}
    assert verdicts["You cannot use venture capital to force breakthroughs"] == SUPPORTED
    assert verdicts["You cannot use venture capital to force miracles"] == REFUTED
    assert verdicts["that is about 27 mid-range GPUs"] == SUPPORTED
    assert verdicts["that is about 82 mid-range GPUs"] == REFUTED
    assert verdicts["Science, funded like a startup portfolio"] == SUPPORTED
    assert verdicts["this should worry NVIDIA"] == SUPPORTED
    assert len(pending) == 1 and pending[0]["type"] == FACTUAL
    print("\nchecks.claims OK (routing only; entailment needs a model)")
