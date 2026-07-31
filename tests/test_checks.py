"""Regression cases for the checking layer. Every one is a real failure mode.

Nothing here touches the network or a model: the pure checkers are pure, and
claim routing is exercised on hand-built claims so the classification a model
would produce is separated from what the router does with it.
"""

import pytest

import config
from checks import claims, numbers, quotes, sentences

GPU = config.REFERENCE_SCALES["midrange_gpu_price_usd"]["value"]
SOURCE = ('Powell said, "I think it’s part of a package of rules that the Trump '
          'administration is pushing through," in an interview. The system is '
          'now in full production.')


# --- quotes ----------------------------------------------------------------

def test_verbatim_quote_passes():
    assert quotes.verify("I think it’s part of a package of rules", SOURCE)


def test_quote_with_one_word_changed_drops():
    assert not quotes.verify("I think it’s part of a series of rules", SOURCE)


def test_quote_typography_is_forgiven_but_wording_is_not():
    assert quotes.verify("I think it's part of a package", SOURCE)  # straight '
    assert not quotes.verify("I believe it's part of a package", SOURCE)


def test_quote_spanning_a_line_break_passes():
    assert quotes.verify("in full production", "The system is now in\nfull production.")


def test_invented_quote_drops():
    assert not quotes.verify("We are thrilled to announce this milestone", SOURCE)


# --- numbers ---------------------------------------------------------------

def test_correct_scale_anchor_passes():
    assert numbers.verify(f"15000 / {GPU}", 27.3)


def test_anchor_off_by_3x_drops():
    assert not numbers.verify(f"15000 / {GPU}", 81.8)


def test_anchor_rounded_for_speech_passes():
    assert numbers.verify(f"15000 / {GPU}", 27.0)


def test_anchor_referencing_unknown_scale_drops():
    assert not numbers.verify("15000 / average_uk_salary_gbp", 27.3)


def test_anchor_dividing_by_unmaintained_value_is_flagged():
    # Correct arithmetic, but 400 is a reference value nobody maintains.
    assert numbers.verify("15000 / 400", 37.5)
    assert not numbers.uses_known_reference("15000 / 400")


def test_anchor_expression_never_executes_code():
    assert not numbers.verify("__import__('os').system('echo pwned')", 1.0)


# --- sentence splitting ----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("B200. torch.compile lifts speed.", ["B200.", "torch.compile lifts speed."]),
    ("It ran in 1.68 seconds. Then it crashed.",
     ["It ran in 1.68 seconds.", "Then it crashed."]),
    ("We shipped numpy.array today. Then we reverted.",
     ["We shipped numpy.array today.", "Then we reverted."]),
    ("Only logged-in U.S. residents qualify.",
     ["Only logged-in U.S. residents qualify."]),
    ("Acme Inc. announced a merger.", ["Acme Inc. announced a merger."]),
])
def test_sentence_splitting_regressions(text, expected):
    assert sentences.split(text) == expected


# --- claim routing ---------------------------------------------------------

SLOTS = {
    "quote": {"text": "I think it’s part of a package of rules",
              "name": "Keri Powell", "role": "attorney"},
    "scale_anchor": {"anchor_math": f"15000 / {GPU}", "statement": "about 27 GPUs"},
}


def _claim(kind, text, anaphoric=False):
    return {"sentence_index": 0, "sentence": "", "type": kind,
            "claim": text, "anaphoric": anaphoric}


def _route_one(claim):
    settled, pending = claims.route([claim], SLOTS)
    return settled[0] if settled else {"verdict": "PENDING", **pending[0]}


def test_pure_framing_passes():
    result = _route_one(_claim(claims.FRAMING, "Science, funded like a startup"))
    assert result["verdict"] == claims.SUPPORTED
    assert result["checked_by"] == "type"  # no model call


def test_judgment_survives_and_passes():
    result = _route_one(_claim(claims.JUDGMENT,
                               "this is the part that should worry NVIDIA"))
    assert result["verdict"] == claims.SUPPORTED


def test_anaphoric_claim_resolved_to_fact_reaches_the_model():
    """A pronoun subject is claim-bearing until resolved. "That is a research
    demo" resolves to an assertion about the preceding subject, so the
    extractor types it factual and the router must send it for entailment —
    this is the case that contradicted its own prior sentence in the previous
    build."""
    result = _route_one(_claim(claims.FACTUAL,
                               "the system is a research demo", anaphoric=True))
    assert result["verdict"] == "PENDING"


def test_anaphoric_judgment_survives_resolution():
    """"This is the part that should worry NVIDIA" resolves to a claim about
    significance, not about the world. It must NOT be sent for entailment,
    where it would be refuted for not appearing in the article."""
    result = _route_one(_claim(
        claims.JUDGMENT, "the preceding fact is significant for NVIDIA",
        anaphoric=True))
    assert result["verdict"] == claims.SUPPORTED
    assert result["checked_by"] == "type"


def test_unsupported_detail_reaches_the_model():
    """'without extra fees' — a pricing guarantee invented about a real
    product — must not be settled in Python as framing."""
    result = _route_one(_claim(
        claims.FACTUAL, "It spans free, Go, Plus, and Pro tiers without extra fees"))
    assert result["verdict"] == "PENDING"
    assert "without extra fees" in result["claim"]


def test_quote_routing_uses_python_not_a_model():
    good = _route_one(_claim(claims.QUOTE, "I think it’s part of a package of rules"))
    bad = _route_one(_claim(claims.QUOTE, "I think it’s part of a series of rules"))
    assert good["verdict"] == claims.SUPPORTED
    assert bad["verdict"] == claims.REFUTED
    assert good["checked_by"] == bad["checked_by"] == "checks.quotes"


def test_arithmetic_routing_uses_python_not_a_model():
    good = _route_one(_claim(claims.ARITHMETIC, "that is about 27 mid-range GPUs"))
    bad = _route_one(_claim(claims.ARITHMETIC, "that is about 82 mid-range GPUs"))
    assert good["verdict"] == claims.SUPPORTED
    assert bad["verdict"] == claims.REFUTED
    assert good["checked_by"] == "checks.numbers"


def test_counts_by_type():
    counted = claims.counts_by_type([
        _claim(claims.FACTUAL, "a"), _claim(claims.FACTUAL, "b"),
        _claim(claims.QUOTE, "c")])
    assert counted == {claims.FACTUAL: 2, claims.QUOTE: 1}


# --- erosion floor ---------------------------------------------------------

def test_erosion_floor():
    from stages import check
    first = [{"slot": s} for s in ("lede", "number", "number", "quote", "tension")]
    assert check.erosion_problem(first, first[:3]) is None
    assert check.erosion_problem(first, first[:2]) is not None
    assert check.erosion_problem(first, [{"slot": "tension"}]) is not None
    assert check.erosion_problem(
        first, [{"slot": "lede"}, {"slot": "tension"}]) is not None


def test_arithmetic_without_a_scale_anchor_goes_to_the_checker():
    """A report with no scale_anchor has no anchor arithmetic to recompute —
    which does not make the writer's arithmetic false. Auto-refuting it
    reported "'' does not evaluate: invalid syntax" as though the script were
    malformed, and forced a redraft over correct sums."""
    fixture = [{"sentence_index": 0, "sentence": "x", "type": claims.ARITHMETIC,
                "anaphoric": False,
                "claim": "Opus 5 costs $10 more per million output tokens."}]
    settled, pending = claims.route(fixture, {"scale_anchor": None})
    assert settled == []
    assert len(pending) == 1
    assert pending[0]["type"] == claims.FACTUAL
    assert pending[0]["reclassified_from"] == claims.ARITHMETIC


def test_arithmetic_with_an_anchor_is_still_settled_in_python():
    fixture = [{"sentence_index": 0, "sentence": "x", "type": claims.ARITHMETIC,
                "anaphoric": False, "claim": "that is about 27 mid-range GPUs"}]
    settled, pending = claims.route(
        fixture, {"scale_anchor": {"anchor_math": "15000 / 550",
                                   "statement": "about 27 GPUs"}})
    assert pending == []
    assert settled[0]["checked_by"] == "checks.numbers"
