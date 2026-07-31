"""Sentence splitting. Pure, shared by draft.py and claims.py.

WHITESPACE, NEVER CAPITALISATION. A period followed by whitespace is a
boundary; a period with no whitespace after it is inside an identifier or a
number. Scripts in this niche legitimately begin sentences with
torch.compile, npm and pip, so next-word capitalisation is not a signal —
using it silently merged "B200. torch.compile lifts speed" into one sentence
in the previous build.
"""

import re

# Genuine exceptions, where a period plus whitespace is NOT a boundary.
ABBREVIATIONS = ("u.s.", "u.k.", "e.g.", "i.e.", "inc.", "ltd.", "corp.",
                 "co.", "vs.", "dr.", "mr.", "mrs.", "ms.", "prof.", "st.",
                 "jr.", "sr.", "etc.", "fig.", "no.", "approx.", "est.")

# A terminator, any closing quote/bracket that trails it, then whitespace.
# The boundary falls AFTER the closing mark so a sentence-final quotation
# stays with the sentence it closes.
_BOUNDARY_RE = re.compile(r'[.!?]["\'”’)\]]*(?=\s)')

# A CLOSING QUOTATION MARK followed by whitespace and a capital letter is also
# a boundary, even with no terminator inside the quote. Source quotes arrive
# without terminal punctuation once a dangling comma is trimmed ('"...HAWK is
# dead," she says' -> "...HAWK is dead"), and the script then reads
#   Sophie Schmieg: "Basically with this paper, HAWK is dead" The cost was...
# which fused a quotation and a separate fact into one 20-word sentence and
# tripped the passthrough gate.
#
# This does NOT reintroduce capitalisation-based splitting: the signal is the
# closing quote, and a bare period keeps the whitespace rule. `torch.compile`
# and `npm install` contain no quotation mark, so they are untouched.
_QUOTE_BOUNDARY_RE = re.compile(r'(?<=[^\s.!?])["”](?=\s+[A-Z])')


def _boundaries(text: str):
    """Every split point, in order: terminators, plus unpunctuated closing
    quotations."""
    ends = {m.end() for m in _BOUNDARY_RE.finditer(text)}
    ends |= {m.end() for m in _QUOTE_BOUNDARY_RE.finditer(text)}
    return sorted(ends)


def split(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for end in _boundaries(text):
        candidate = text[start:end]
        words = candidate.split()
        last_word = words[-1].lower() if words else ""
        if last_word in ABBREVIATIONS:
            continue  # "logged-in U.S. residents" is not two sentences
        sentences.append(candidate)
        start = end
    if text[start:].strip():
        sentences.append(text[start:])
    return [s.strip() for s in sentences if s.strip()]


if __name__ == "__main__":
    # Every case here is a real failure mode, several from CLAUDE.md.
    cases = [
        ("B200. torch.compile lifts speed by 30%.",
         ["B200.", "torch.compile lifts speed by 30%."]),
        ("It ran in 1.68 seconds. Then it crashed.",
         ["It ran in 1.68 seconds.", "Then it crashed."]),
        ("We shipped numpy.array today. Then we reverted.",
         ["We shipped numpy.array today.", "Then we reverted."]),
        ("Only logged-in U.S. residents qualify.",
         ["Only logged-in U.S. residents qualify."]),
        ("Acme Inc. announced a merger.",
         ["Acme Inc. announced a merger."]),
        ("Costs fell 3.5 percent in the U.S. last year.",
         ["Costs fell 3.5 percent in the U.S. last year."]),
        ('She said "no." Then she left.',
         ['She said "no."', "Then she left."]),
        ("Ship it! Now. Really?", ["Ship it!", "Now.", "Really?"]),
        ("Use e.g. this one. Done.", ["Use e.g. this one.", "Done."]),
        ("No trailing terminator", ["No trailing terminator"]),
        ("It costs $5.25 per token. npm install works now.",
         ["It costs $5.25 per token.", "npm install works now."]),
        # A source quote whose dangling comma was trimmed has no terminal
        # punctuation, so the quotation and the next fact fused into one
        # 20-word sentence and tripped the passthrough gate.
        ('Sophie Schmieg: "HAWK is dead" The cost was $100,000.',
         ['Sophie Schmieg: "HAWK is dead"', "The cost was $100,000."]),
        # ...but a quotation mid-sentence is not a boundary.
        ('She called it "dead" and moved on.',
         ['She called it "dead" and moved on.']),
    ]
    failures = 0
    for text, expected in cases:
        got = split(text)
        ok = got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {text[:46]:48} -> {len(got)} sentence(s)")
        if not ok:
            print(f"       expected {expected}\n       got      {got}")
    assert not failures, f"{failures} splitter failures"
    print("checks.sentences OK")
