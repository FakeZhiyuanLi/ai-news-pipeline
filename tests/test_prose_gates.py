"""The notes-vs-copy gates, pinned in both directions.

The reference strings are real: the NOTES are what report.py produced for run
20260727T073711Z-02b8, the PASSTHROUGH is what draft.py used to emit from
them, and the COPY is what it emits now.
"""

import pytest

from checks import numbers, prose

NOTES = (
    "The Trump administration allocated $5 billion to AI-focused research grants "
    "while proposing a policy plan that shifts federal science funding away from "
    "universities toward individual researchers and private companies. "
    "Researchers warn that shifting public science funding to a venture-capital "
    "model risks eroding basic research. "
    "The policy manifesto contains contradictions, such as cutting life sciences "
    "funding while boosting foundational biological research."
)
QUOTE = ("You cannot use venture capital to force breakthroughs while virtually "
         "already selling the patent.")


def test_passthrough_is_rejected():
    sentence = ("The Trump administration allocated $5 billion to AI-focused "
                "research grants while proposing a policy plan that shifts "
                "federal science funding away from universities.")
    findings = prose.find_passthrough([sentence], NOTES)
    assert findings and findings[0]["run_words"] >= prose.MAX_VERBATIM_RUN


def test_copy_written_from_the_facts_passes():
    copy = ["Washington just put $5 billion into AI research.",
            "5,000 labs applied.",
            "278 got it.",
            "And the money skips universities entirely.",
            "Science, funded like a startup portfolio."]
    assert prose.find_passthrough(copy, NOTES) == []


def test_verbatim_quote_is_exempt():
    sentence = f'AI policy professor Claes de Vreese: "{QUOTE}"'
    notes_with_quote = NOTES + " " + QUOTE
    assert prose.find_passthrough([sentence], notes_with_quote, exempt=[QUOTE]) == []
    # Without the exemption the same sentence trips the gate, proving the
    # exemption is what saves it rather than a short run.
    assert prose.find_passthrough([sentence], notes_with_quote) != []


def test_number_and_name_runs_are_not_passthrough():
    proper = {"Trump", "Genesis", "Mission"}
    sentence = "$5 billion for Genesis Mission grants went out."
    notes = "$5 billion for Genesis Mission grants"
    assert prose.find_passthrough([sentence], notes, proper_nouns=proper) == []


@pytest.mark.parametrize("lengths,should_fail", [
    ([9, 3, 3, 15, 6], False),        # varied: the target shape
    ([8, 3, 3, 15, 20, 19], False),   # a real accepted draft
    ([9, 7, 14, 13, 7], False),       # varied enough; the mean-band rule wrongly killed this
    ([6, 13, 4, 10], False),          # ditto — 9-word range with a 4-word line
    ([29, 25, 12, 20, 23], True),     # what draft.py used to produce
    ([9, 9, 9, 9], True),             # no short sentence, and no variation
    ([21, 5], True),                  # one sentence over the ceiling
    ([10, 10, 11, 10], True),         # spread under 2.5 and range under 6
])
def test_rhythm_rules(lengths, should_fail):
    sentences = [" ".join(["word"] * n) for n in lengths]
    problems = prose.rhythm_problems(sentences)
    assert bool(problems) == should_fail, (lengths, problems)


@pytest.mark.parametrize("expression,useful", [
    ("5000000000 / 66600", False),   # 75,075 salaries — a second big number
    ("490 / 500", True),             # ~1 phone
    ("15000 / 550", True),           # ~27 GPUs
    ("5 / 66600", False),            # vanishingly small
    ("550 / 0", False),              # does not evaluate
    ("__import__('os').system('x')", False),  # never eval()
])
def test_anchor_usefulness(expression, useful):
    ok, _reason = numbers.anchor_is_useful({"anchor_math": expression})
    assert ok is useful, expression
