"""Hook, funnel order, closer, caption and beat kinds.

These are the four gaps the first watchable video exposed. Each is checked in
Python because none of them can be enforced by asking: a lede and a hook are
both "the first sentence", and a model told to write a hook will write a lede
and label it one.
"""

import pytest

import config
from stages import draft

SLOTS = {
    "lede": "The administration allocated $5 billion to AI research grants.",
    "numbers": ["$5 billion for grants", "278 projects selected",
                "over 5,000 applications submitted"],
    "tension": "The plan cuts life sciences while boosting foundational biology.",
}


def _beat(kind, sentence, slot="number"):
    return {"slot": slot, "kind": kind, "sentence": sentence,
            "tts_sentence": sentence}


# --- beat kinds ------------------------------------------------------------

def test_every_beat_gets_a_kind():
    """`beats: [None, None, None]` is what this replaces."""
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "kind": "catch", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Federal AI money went mostly unspent")
    assert built["beat_kinds"] == ["hook", "catch"]
    assert all(b["kind"] is not None for b in built["beats"])


def test_a_missing_kind_is_inferred_rather_than_left_null():
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Federal AI money went mostly unspent")
    assert built["beat_kinds"] == ["hook", "catch"]


def test_the_first_beat_must_be_the_hook():
    problems = draft.kind_problems([_beat("proof", "278 were selected."),
                                    _beat("catch", "The plan contradicts itself.")])
    assert any("not 'hook'" in p for p in problems)


def test_the_last_beat_must_be_a_closer_or_the_catch():
    problems = draft.kind_problems([_beat("hook", "5,000 applied."),
                                    _beat("proof", "278 were selected.")])
    assert any("mid-proof" in p for p in problems)


def test_funnel_is_a_distinct_kind_from_proof():
    """The renderer treats a paired-number beat differently, so the two must
    not collapse into one label."""
    assert "funnel" in config.BEAT_KINDS and "proof" in config.BEAT_KINDS
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "number", "kind": "funnel", "sentence": "278 got it.",
         "tts_sentence": "278 got it."},
        {"slot": "tension", "kind": "catch",
         "sentence": "The plan never says where the line falls.",
         "tts_sentence": "The plan never says where the line falls."},
    ], "Federal AI money went mostly unspent")
    assert built["beat_kinds"] == ["hook", "funnel", "catch"]


# --- the closer ------------------------------------------------------------

def test_closer_must_not_be_longer_than_the_shortest_line():
    """8 words is inside the ceiling, so this fails only on the comparison —
    the script already has a 3-word line, and a longer landing does not
    bookend anything."""
    beats = [_beat("hook", "Over 5,000 projects applied for federal AI money."),
             _beat("funnel", "278 won."),
             _beat("catch", "The plan cuts one field while boosting another."),
             _beat("closer", "Most of those applications lost out entirely today.")]
    problems = draft.closer_problems(beats)
    assert any("shortest other sentence" in p for p in problems), problems


def test_closer_under_the_word_ceiling_passes():
    beats = [_beat("hook", "Over 5,000 projects applied for federal AI money."),
             _beat("catch", "The plan cuts one field while boosting another."),
             _beat("closer", "Most applications lost.")]
    assert draft.closer_problems(beats) == []


@pytest.mark.parametrize("sentence", [
    "The future of science funding.",
    "One thing is clear.",
    "Time will tell.",
    "We will be watching.",
    "You should be worried.",
])
def test_filler_closers_are_rejected(sentence):
    """Where the notes cannot support a real bookend the answer is to omit
    the closer, never to reach for one of these."""
    beats = [_beat("hook", "Over 5,000 projects applied for federal AI money."),
             _beat("catch", "The plan cuts one field while boosting another."),
             _beat("closer", sentence)]
    assert draft.closer_problems(beats), f"{sentence!r} should be rejected"


def test_omitting_the_closer_is_a_correct_result():
    """A script ending on a strong catch beats one padded with filler."""
    beats = [_beat("hook", "Over 5,000 projects applied for federal AI money."),
             _beat("catch", "The plan cuts one field while boosting another.")]
    assert draft.closer_problems(beats) == []
    assert draft.kind_problems(beats) == []


def test_the_closer_is_never_dropped_by_the_beat_budget():
    """Truncation drops ranked proof beats. Dropping the closer would leave
    the script stopping mid-thought, which is the shape this exists to fix."""
    beats = [_beat("hook", "Over 5,000 projects applied.")]
    beats += [_beat("proof", f"Number {i} landed here somewhere today.")
              for i in range(6)]
    beats.append(_beat("closer", "Most lost."))
    kept, dropped = draft._truncate_to_budget(beats)
    assert kept[-1]["kind"] == "closer"
    assert all(b["kind"] == "proof" for b in dropped)


# --- the caption -----------------------------------------------------------

def test_caption_must_not_repeat_the_first_sentence():
    """Subtitles already show that sentence; repeating it spends the strongest
    real estate in the frame on nothing."""
    problems = draft.caption_problems("5,000 labs asked for AI money",
                                      "5,000 labs asked for AI money today.")
    assert any("repeats the first sentence" in p for p in problems)


def test_caption_sharing_three_words_is_allowed():
    problems = draft.caption_problems("Federal AI money mostly went unspent",
                                      "5,000 labs asked Washington for money.")
    assert problems == []


@pytest.mark.parametrize("caption,ok", [
    ("Four words only here", False),
    ("Five words exactly right here now", True),
    ("Eight words is the ceiling for a caption", True),
    # Nine is allowed: the bench showed the overrun is always exactly one
    # word, and losing the draft over it buys no legibility.
    ("Nine words sits right at the stated ceiling now", True),
    ("Ten words is finally one word over the stated ceiling", False),
])
def test_caption_word_count(caption, ok):
    problems = draft.caption_problems(caption, "Unrelated opening sentence entirely.")
    assert (problems == []) is ok, problems


def test_caption_is_cleaned_of_emoji_hashtags_and_terminal_period():
    assert draft.clean_caption("Federal AI money mostly went unspent. 🚀 #AI") == \
        "Federal AI money mostly went unspent"


def test_caption_never_reaches_the_spoken_text():
    """It is displayed, not narrated. If it reached script_text it would be
    read aloud and subtitled."""
    with pytest.raises(RuntimeError, match="appears in the spoken script"):
        draft.build_draft(SLOTS, [
            {"slot": "lede", "kind": "hook",
             "sentence": "Federal AI money mostly went unspent.",
             "tts_sentence": "Federal AI money mostly went unspent."},
            {"slot": "tension", "kind": "catch", "sentence": "Nobody says why.",
             "tts_sentence": "Nobody says why."},
        ], "Federal AI money mostly went unspent")


def test_caption_is_absent_from_tts_text():
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "kind": "catch", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Federal AI money mostly went unspent")
    assert built["caption"] not in built["tts_text"]
    assert built["caption"] not in built["script_text"]


def test_caption_repair_fixes_only_the_caption(monkeypatch):
    """Five independent gates each failing ~17% of the time makes a clean
    draft improbable. The caption is one field and independent of the prose,
    so a bad one is repaired against the already-good script."""
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "kind": "catch", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Too short")                      # 2 words: below the floor
    assert draft.caption_problems(built["caption"], built["sentences"][0])

    monkeypatch.setattr(draft.llm, "json_call",
                        lambda *a, **k: {"caption": "Federal AI money mostly went unspent"})
    repaired = draft._repair_caption(built, SLOTS, None, label="t")
    assert repaired["caption"] == "Federal AI money mostly went unspent"
    assert repaired["script_text"] == built["script_text"]  # prose untouched


def test_caption_repair_refuses_a_caption_that_is_in_the_spoken_text(monkeypatch):
    """The repair must not become the route around the never-spoken rule."""
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "kind": "catch", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Too short")
    monkeypatch.setattr(draft.llm, "json_call",
                        lambda *a, **k: {"caption": "Most got nothing at all"})
    repaired = draft._repair_caption(built, SLOTS, None, label="t")
    assert repaired["caption"] == "Too short"  # rejected, original kept


def test_caption_repair_is_skipped_when_the_caption_is_fine(monkeypatch):
    built = draft.build_draft(SLOTS, [
        {"slot": "lede", "kind": "hook", "sentence": "5,000 labs applied.",
         "tts_sentence": "5,000 labs applied."},
        {"slot": "tension", "kind": "catch", "sentence": "Most got nothing at all.",
         "tts_sentence": "Most got nothing at all."},
    ], "Federal AI money mostly went unspent")

    def explode(*a, **k):
        raise AssertionError("no model call when the caption is already valid")

    monkeypatch.setattr(draft.llm, "json_call", explode)
    assert draft._repair_caption(built, SLOTS, None, label="t") is built


# --- worked-example leakage ------------------------------------------------

def test_the_prompts_worked_example_is_rejected_as_a_draft():
    """Under the combined load of a forbidden-claims preamble, the previous
    draft and a named defect, the writer returned "5,000 labs asked Washington
    for AI money" as the only beat of a script about post-quantum crypto. The
    example is a complete, correct-looking script, so it is what a struggling
    model reaches for — and unlike every other gate, this one guards against
    narrating a fact from an unrelated story."""
    crypto_slots = {
        "lede": "An AI system broke a post-quantum cryptography candidate.",
        "numbers": ["about $100,000 in compute", "60 hours"],
    }
    leaked = draft.example_leakage(
        "5,000 labs asked Washington for AI money. 278 got it.", crypto_slots)
    assert leaked, "example text must be caught"


def test_example_spans_are_allowed_when_the_story_really_is_that_story():
    """The example story could genuinely come round again, so a span is only
    leakage when the notes do not contain it."""
    real_slots = {
        "lede": "Washington allocated $5 billion in Genesis Mission grants.",
        "numbers": ["over 5,000 applications", "278 projects selected"],
        "quote": {"text": "You cannot use venture capital to force breakthroughs",
                  "name": "Claes de Vreese", "role": "AI policy professor"},
    }
    assert draft.example_leakage("5,000 labs asked Washington for AI money.",
                                 real_slots) == []


def test_a_single_hook_beat_is_reported_as_a_collapsed_draft():
    problems = draft.kind_problems([_beat("hook", "5,000 labs applied.")])
    assert any("single" in p and "not a script" in p for p in problems)
