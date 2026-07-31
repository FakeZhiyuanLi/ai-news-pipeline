"""The notes-vs-copy gates, pinned in both directions.

The reference strings are real: the NOTES are what report.py produced for run
20260727T073711Z-02b8, the PASSTHROUGH is what draft.py used to emit from
them, and the COPY is what it emits now.
"""

import pytest

from checks import numbers, prose, sentences

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


def test_a_multi_sentence_quote_keeps_its_length_exemption():
    """The splitter divides a two-sentence quote before the rhythm check sees
    it, so looking for the whole span inside one sentence never matched and
    the exemption silently vanished — which made every story with a
    two-sentence quote structurally undraftable."""
    quote = ("We continue to work with our government partners to conduct "
             "their own independent testing of our models. This includes "
             "Opus 5.")
    parts = sentences.split(f'Anthropic spokesperson Danielle Ghiglieri: "{quote}"')
    assert len(parts) > 1, "this test is only meaningful if the quote splits"
    assert prose.rhythm_problems(parts + ["Short line."], exempt_long=[quote]) == []


def test_the_quote_exemption_is_not_a_loophole():
    """A long sentence that merely echoes a few of the quote's words must
    still be rejected for its length."""
    quote = ("We continue to work with our government partners to conduct "
             "their own independent testing of our models.")
    sneaky = ("We continue to believe this rambling sentence about nothing in "
              "particular whatsoever should absolutely still be rejected purely "
              "for its excessive length.")
    problems = prose.rhythm_problems([sneaky, "Short."], exempt_long=[quote])
    assert any("over 20 words" in p for p in problems)


def test_an_unavoidable_identifier_is_not_passthrough():
    """"38.3% on the ARC-AGI-3 public set" is 8 tokens with no paraphrase —
    every content word is a figure or a benchmark name. Counting raw tokens
    rejected it three redrafts running and made the story undraftable."""
    notes = ("GPT-5.6 Sol scored 38.3% on the ARC-AGI-3 public set with retained "
             "reasoning and compaction. With the official harness, it scored "
             "13.3% on the same public set.")
    names = {"gpt", "sol", "arc", "agi", "3"}
    assert prose.find_passthrough(
        ["It hit 38.3% on the ARC-AGI-3 public set."], notes,
        proper_nouns=names) == []


def test_the_notes_read_aloud_are_still_passthrough():
    """The exemption must not swallow the case the gate exists for."""
    notes = ("GPT-5.6 Sol scored 38.3% on the ARC-AGI-3 public set with retained "
             "reasoning and compaction.")
    names = {"gpt", "sol", "arc", "agi", "3"}
    assert prose.find_passthrough(
        ["GPT-5.6 Sol scored 38.3% on the ARC-AGI-3 public set with retained "
         "reasoning and compaction."], notes, proper_nouns=names) != []


def test_ordinary_prose_still_trips_at_eight_words():
    notes = ("Evals rarely measure models in isolation, they also measure a "
             "bundle of less visible choices about API settings.")
    assert prose.find_passthrough(
        ["Evals rarely measure models in isolation, they also measure a bundle."],
        notes) != []


def test_caption_duplication_is_still_caught_by_a_run_of_names():
    """The caption gate asks the OPPOSITE question — is this the same text
    twice — so a repeated run of figures and names is the duplication it must
    catch, and it must not inherit the passthrough exemption."""
    run, _ = prose.longest_shared_run("5,000 labs asked for AI money",
                                      "5,000 labs asked for AI money today.")
    assert run >= 4
