"""Verbatim quote verification. A PURE FUNCTION — no network, no model, no
filesystem.

A quote either appears in the source text or it does not. That is a substring
search, and it must never be delegated to a model: a model asked "is this
quote accurate?" will forgive a changed word, and a changed word inside
quotation marks attributed to a named person is the most damaging error this
pipeline can make.

Whitespace is normalised because line wrapping and HTML extraction insert
breaks that carry no meaning. Typography is normalised because publishers
write ’ and writers type ' — demanding the curly form would reject text no
human would produce. Nothing else is forgiven: one different WORD drops the
quote.
"""

import re

_SMART_PUNCTUATION = str.maketrans({
    "’": "'", "‘": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "‑": "-",
    " ": " ", " ": " ", "​": "",
    "…": "...",
})


def canonical(text: str) -> str:
    """Whitespace-normalised, typography-normalised text for comparison."""
    return re.sub(r"\s+", " ", (text or "").translate(_SMART_PUNCTUATION)).strip()


def verify(quote: str, source_text: str) -> bool:
    """Does this quote appear character for character in the source?

    Case IS significant — "He said" and "he said" differ — but leading and
    trailing punctuation on the quote itself is not, since a quote lifted
    from '"...constructed," she says' arrives with the comma attached.
    """
    needle = canonical(quote).strip('"\'').strip()
    needle = needle.rstrip(",;:").strip()
    if not needle:
        return False
    return needle in canonical(source_text)


def find_altered(quote: str, source_text: str) -> str | None:
    """Best-effort explanation of WHY a quote failed, for the error message.
    Returns None if the quote verifies."""
    if verify(quote, source_text):
        return None
    words = canonical(quote).strip('"\'').split()
    if not words:
        return "the quote is empty"
    haystack = canonical(source_text)
    # Longest prefix that still appears tells us roughly where it diverged.
    longest = 0
    for n in range(1, len(words) + 1):
        if " ".join(words[:n]) in haystack:
            longest = n
        else:
            break
    if longest == 0:
        return "no part of this quote appears in the source"
    if longest == len(words):
        return "the quote appears but with different surrounding punctuation"
    return (f"the quote diverges from the source after {longest} word(s): "
            f"...{' '.join(words[max(0, longest - 4):longest])} "
            f"[{words[longest]}]")


if __name__ == "__main__":
    source = ('Powell said, "I think it’s part of a package of rules that the '
              'Trump administration is pushing through to make it easier for AI '
              'data centers to be constructed," in an interview.\n\n'
              'She added that states retain\ntheir own authority.')

    exact = "I think it’s part of a package of rules"
    assert verify(exact, source)
    print(f"ok   verbatim quote passes")

    straight = "I think it's part of a package of rules"  # writer types ' not ’
    assert verify(straight, source)
    print(f"ok   straight apostrophe accepted for curly source")

    wrapped = "states retain their own authority"  # newline in the source
    assert verify(wrapped, source)
    print(f"ok   quote spanning a line break passes")

    trailing = 'I think it’s part of a package of rules that the Trump administration is pushing through to make it easier for AI data centers to be constructed,'
    assert verify(trailing, source)
    print(f"ok   trailing comma on the extracted quote tolerated")

    changed = "I think it’s part of a series of rules"  # package -> series
    assert not verify(changed, source)
    print(f"ok   one changed word drops: {find_altered(changed, source)}")

    invented = "This was a complete fabrication by the assistant"
    assert not verify(invented, source)
    print(f"ok   invented quote drops: {find_altered(invented, source)}")

    assert not verify("", source) and not verify("anything", "")
    print("ok   empty inputs drop")
    print("checks.quotes OK")
