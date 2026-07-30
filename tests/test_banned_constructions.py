"""The wire-copy detector, pinned against both directions of failure.

Every MUST_PASS string is real draft text that the detector once flagged.
A false positive is worse than a miss here: a flag that fires on correct
prose trains the eye to ignore the flag, and then the true positives go
unread too.
"""

import pytest

from stages.draft import find_banned

MUST_FLAG = [
    ("Something changed today. Anthropic released its Opus 5 AI model.",
     "company name followed by announced/unveiled/released/launched"),
    ("Google unveiled a new model.",
     "company name followed by announced/unveiled/released/launched"),
    ("The feature is designed to help developers ship faster.", '"designed to"'),
    ("Like and subscribe for more AI news.", "call to action"),
    ("Hit that like button.", "call to action"),
    ("Comment below with your thoughts.", "call to action"),
    ("Follow us for daily updates.", "call to action"),
    ("Thanks for watching, see you tomorrow.", "sign-off"),
    ("Let's dive in.", '"let\'s dive in"'),
    ("As she put it, quote, it works, unquote.",
     '"quote"/"unquote" spoken as narration'),
    ("This is the biggest change in years.",
     "superlative outside an attributed quotation"),
]

# Real draft sentences that were false-flagged before the patterns were fixed.
MUST_PASS = [
    "As Bill Oliver put it, he had asked for a version that reads like a "
    "legislative speech rather than a series of short points.",
    "Anthropic intentionally omitted cutting-edge training on cybersecurity "
    "tasks, leaving Opus 5 substantially behind Mythos 5.",
    "Publishers share concerns about collapsing search traffic.",
    "The agent breached four accounts on four separate services.",
    "The tool behaves like a compiler for prompts.",
    "Researchers share findings openly under a permissive licence.",
    'She called it "the best result we have seen", which the paper supports.',
]


@pytest.mark.parametrize("text,expected", MUST_FLAG)
def test_banned_constructions_are_caught(text, expected):
    assert expected in find_banned(text), f"missed {expected!r} in: {text}"


@pytest.mark.parametrize("text", MUST_PASS)
def test_correct_prose_is_not_flagged(text):
    assert find_banned(text) == [], f"false positive on: {text}"
